from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.errors import CheckViolation
from pydantic import Field

from database import get_db
from files import record_file_activity, stream_file
from teams import current_team_account
from workflows import StrictInput, date_clause
from datetime import date


class VersionInput(StrictInput):
    name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0, le=4 * 1024 ** 4, strict=True)
    upload_key: UUID
    base_version: int = Field(ge=1, strict=True)


def version_access(connection, account, file_id, *, edit=False):
    record = connection.execute("""SELECT file.* FROM stored_file file
        JOIN storage_connection storage ON storage.id=file.connection_id AND storage.enabled
        JOIN team_account owner ON owner.id=file.owner_id AND owner.company_id=file.company_id
            AND owner.team_id=file.team_id AND owner.status='active'
        WHERE file.id=%s AND file.company_id=%s AND file.state='ready'
        FOR UPDATE OF file FOR SHARE OF owner,storage""", (file_id, account['company_id'])).fetchone()
    if not record:
        raise HTTPException(404, 'File not found.')
    active = connection.execute("SELECT id FROM team_account WHERE id=%s AND company_id=%s AND team_id=%s AND status='active' FOR SHARE",
        (account['id'], account['company_id'], account['team_id'])).fetchone()
    grant = connection.execute('SELECT permission FROM file_share WHERE file_id=%s AND company_id=%s AND recipient_id=%s FOR SHARE',
        (file_id, account['company_id'], account['id'])).fetchone()
    owner = record['owner_id'] == account['id']
    if not active or not (owner or grant):
        raise HTTPException(404, 'File not found.')
    record['is_owner'] = owner
    record['can_edit'] = not record['public_access'] and (owner or grant['permission'] == 'edit')
    if edit and not record['can_edit']:
        raise HTTPException(403, 'Restricted Edit access is required.')
    return record


def initial_version(connection, file):
    connection.execute("""INSERT INTO file_version(file_id,company_id,team_id,uploader_id,number,base_version,
        connection_id,object_key,object_version,etag,name,size_bytes,quota_bytes,state,upload_key,created_at,uploaded_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,'ready',%s,%s,%s)
        ON CONFLICT(file_id,number) DO NOTHING""", (file['id'], file['company_id'], file['team_id'], file['owner_id'],
        file['current_version'], file['current_version'], file['connection_id'], file['object_key'], file['object_version'],
        file['etag'], file['name'], file['size_bytes'], uuid4(), file['created_at'], file['uploaded_at']))


def publish_version(connection, account, version, metadata, object_version):
    file = version_access(connection, account, version['file_id'], edit=True)
    if file['current_version'] != version['base_version']:
        raise HTTPException(409, 'A newer version is available. Refresh version history before uploading again.')
    connection.execute("UPDATE file_version SET quota_bytes=0,etag=%s,object_version=%s,state='ready',uploaded_at=clock_timestamp(),multipart_upload_id=NULL,multipart_part_bytes=NULL WHERE id=%s",
        (metadata['ETag'], object_version, version['id']))
    connection.execute("""UPDATE stored_file SET object_key=%s,object_version=%s,etag=%s,size_bytes=%s,
        current_version=%s,uploaded_at=clock_timestamp() WHERE id=%s""",
        (version['object_key'], object_version, metadata['ETag'], version['size_bytes'], version['number'], file['id']))
    connection.execute('UPDATE file_version SET quota_bytes=size_bytes WHERE file_id=%s AND number=%s', (file['id'], file['current_version']))
    record_file_activity(connection, account, file['id'], 'version_uploaded', f"Uploaded version {version['number']}.")
    return {'id': version['id'], 'state': 'ready', 'detail': 'New version uploaded.'}


def create_version_router(origin_dependency):
    router = APIRouter(prefix='/team/files')
    mutation = [Depends(origin_dependency)]

    @router.get('/{file_id}/versions')
    def versions(file_id: UUID, page: int = Query(1, ge=1, le=100000), from_date: date | None = None,
                 to_date: date | None = None, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        file = version_access(connection, account, file_id)
        initial_version(connection, file)
        dates, values = date_clause(from_date, to_date, 'version.created_at')
        where = f'file_id=%s AND {dates}'
        total = connection.execute(f'SELECT count(*) AS total FROM file_version version WHERE {where}', (file_id, *values)).fetchone()['total']
        items = connection.execute(f"""SELECT version.id,version.number,version.name,version.size_bytes,version.state,
            version.created_at,version.uploaded_at,person.name AS uploaded_by,version.uploader_id=%s AS own_upload,
            version.base_version FROM file_version version JOIN team_account person ON person.id=version.uploader_id
            WHERE {where} ORDER BY number DESC LIMIT 10 OFFSET %s""", (account['id'], file_id, *values, (page-1)*10)).fetchall()
        policy = connection.execute('SELECT enabled,max_file_bytes FROM company_upload_policy WHERE company_id=%s AND connection_id=%s',
            (file['company_id'], file['connection_id'])).fetchone()
        return {'items': items, 'total': total, 'current_version': file['current_version'], 'can_edit': file['can_edit'],
                'is_owner': file['is_owner'], 'policy': policy}

    @router.post('/{file_id}/versions', status_code=201, dependencies=mutation)
    def reserve_version(file_id: UUID, payload: VersionInput, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        try:
            with connection.transaction():
                file = version_access(connection, account, file_id, edit=True)
                if payload.name != file['name']:
                    raise HTTPException(422, 'Choose a revised file with the same filename.')
                existing = connection.execute('SELECT * FROM file_version WHERE file_id=%s AND uploader_id=%s AND upload_key=%s',
                    (file_id, account['id'], payload.upload_key)).fetchone()
                if existing:
                    if existing['size_bytes'] != payload.size_bytes or existing['base_version'] != payload.base_version:
                        raise HTTPException(409, 'Upload key already used for another revision.')
                    return {'id': existing['id'], 'state': existing['state']}
                if file['current_version'] != payload.base_version:
                    raise HTTPException(409, 'A newer version is available. Refresh version history.')
                policy = connection.execute('SELECT * FROM company_upload_policy WHERE company_id=%s AND connection_id=%s AND enabled FOR SHARE',
                    (file['company_id'], file['connection_id'])).fetchone()
                if not policy:
                    raise HTTPException(409, 'Your company admin must enable uploads first.')
                if payload.size_bytes > policy['max_file_bytes']:
                    raise HTTPException(413, 'File exceeds the company upload limit.')
                initial_version(connection, file)
                number = connection.execute('SELECT max(number)+1 AS number FROM file_version WHERE file_id=%s', (file_id,)).fetchone()['number']
                identifier = uuid4()
                connection.execute("""INSERT INTO file_version(id,file_id,company_id,team_id,uploader_id,number,base_version,
                    connection_id,object_key,name,size_bytes,quota_bytes,upload_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (identifier, file_id, file['company_id'], file['team_id'],
                    account['id'], number, file['current_version'], file['connection_id'], f"mikan/{file['company_id']}/{file['owner_id']}/versions/{identifier}",
                    payload.name, payload.size_bytes, payload.size_bytes, payload.upload_key))
                return {'id': identifier, 'state': 'pending'}
        except CheckViolation as error:
            raise HTTPException(409, 'Team storage allocation exceeded. Previous versions count towards storage.') from error

    @router.get('/{file_id}/versions/{version_id}/content')
    def version_content(file_id: UUID, version_id: UUID, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        version_access(connection, account, file_id)
        record = connection.execute("""SELECT version.name AS filename,version.size_bytes,version.object_key,version.object_version,
            version.etag,storage.* FROM file_version version JOIN storage_connection storage ON storage.id=version.connection_id AND storage.enabled
            WHERE version.id=%s AND version.file_id=%s AND version.state='ready'""", (version_id, file_id)).fetchone()
        if not record:
            raise HTTPException(404, 'Version not found.')
        return stream_file(record)

    @router.post('/{file_id}/versions/{version_id}/restore', dependencies=mutation)
    def restore_version(file_id: UUID, version_id: UUID, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        with connection.transaction():
            file = version_access(connection, account, file_id, edit=True)
            if not file['is_owner']:
                raise HTTPException(403, 'Only the file owner can restore versions.')
            version = connection.execute("SELECT * FROM file_version WHERE id=%s AND file_id=%s AND state='ready' FOR UPDATE", (version_id, file_id)).fetchone()
            if not version:
                raise HTTPException(404, 'Version not found.')
            if version['number'] == file['current_version']:
                return {'detail': 'This version is already current.'}
            number = connection.execute('SELECT max(number)+1 AS number FROM file_version WHERE file_id=%s', (file_id,)).fetchone()['number']
            connection.execute('UPDATE file_version SET quota_bytes=0 WHERE file_id=%s AND object_key=%s', (file_id, version['object_key']))
            connection.execute('UPDATE stored_file SET object_key=%s,object_version=%s,etag=%s,size_bytes=%s,current_version=%s WHERE id=%s',
                (version['object_key'], version['object_version'], version['etag'], version['size_bytes'], number, file_id))
            if version['object_key'] != file['object_key']:
                connection.execute('UPDATE file_version SET quota_bytes=size_bytes WHERE file_id=%s AND number=%s', (file_id, file['current_version']))
            connection.execute("""INSERT INTO file_version(file_id,company_id,team_id,uploader_id,number,base_version,connection_id,
                object_key,object_version,etag,name,size_bytes,quota_bytes,state,upload_key,uploaded_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,'ready',%s,clock_timestamp())""", (file_id, file['company_id'], file['team_id'], account['id'],
                number, file['current_version'], version['connection_id'], version['object_key'], version['object_version'], version['etag'],
                version['name'], version['size_bytes'], uuid4()))
            record_file_activity(connection, account, file_id, 'version_restored', f"Restored version {version['number']} as version {number}.")
        return {'detail': 'Version restored.'}

    @router.get('/{file_id}/activity')
    def activity(file_id: UUID, page: int = Query(1, ge=1, le=100000), from_date: date | None = None,
                 to_date: date | None = None, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        version_access(connection, account, file_id)
        dates, values = date_clause(from_date, to_date)
        where = f'file_id=%s AND {dates}'
        total = connection.execute(f'SELECT count(*) AS total FROM file_activity WHERE {where}', (file_id, *values)).fetchone()['total']
        items = connection.execute(f'SELECT id,actor,action,detail,created_at FROM file_activity WHERE {where} ORDER BY created_at DESC,id DESC LIMIT 10 OFFSET %s',
            (file_id, *values, (page-1)*10)).fetchall()
        return {'items': items, 'total': total}

    return router