import tempfile
from contextlib import closing, contextmanager
from datetime import date
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from psycopg.errors import CheckViolation
from pydantic import Field, field_validator
from starlette.concurrency import run_in_threadpool

from database import get_db, transfer_database
from files import FOLDERS
from storage import StorageInput, destination
from teams import current_team_account
from workflows import StrictInput, date_clause, list_rows, validate_file_target
from zeptomail import cipher


SINGLE_UPLOAD_BYTES = 5_000_000_000
MULTIPART_UPLOAD_BYTES = 4 * 1024 ** 4
MIN_PART_BYTES = 16 * 1024 ** 2
MAX_PARTS = 10000


def multipart_part_bytes(size):
    return max(MIN_PART_BYTES, ((size + MAX_PARTS - 1) // MAX_PARTS + 1048575) // 1048576 * 1048576)


class UploadPolicy(StrictInput):
    enabled: bool = Field(default=False, strict=True)
    max_file_bytes: int = Field(default=MULTIPART_UPLOAD_BYTES, ge=1, le=MULTIPART_UPLOAD_BYTES, strict=True)


class FolderInput(StrictInput):
    name: str = Field(min_length=1, max_length=255)
    parent: str = Field(default='', max_length=255)

    @field_validator('name', 'parent')
    @classmethod
    def clean_target(cls, value, info):
        validate_file_target(value, 'rename' if info.field_name == 'name' else 'move')
        return value


class UploadInput(StrictInput):
    name: str = Field(min_length=1, max_length=255)
    folder: str = Field(default='', max_length=255)
    size_bytes: int = Field(ge=0, le=MULTIPART_UPLOAD_BYTES, strict=True)
    upload_key: UUID

    @field_validator('name', 'folder')
    @classmethod
    def clean_target(cls, value, info):
        validate_file_target(value, 'rename' if info.field_name == 'name' else 'move')
        return value


def storage_client(record):
    _, endpoint = destination(record['provider'], StorageInput(name=record['name'], bucket=record['bucket'], region=record['region'], account_id=record['account_id'], endpoint=record['endpoint'] if record['provider'] == 'r2' else None))
    return boto3.client('s3', endpoint_url=endpoint, region_name=record['region'],
        aws_access_key_id=cipher().decrypt(record['access_key_encrypted'].encode()).decode(),
        aws_secret_access_key=cipher().decrypt(record['secret_key_encrypted'].encode()).decode(),
        config=Config(signature_version='s3v4', connect_timeout=3, read_timeout=60, retries={'total_max_attempts': 1}, proxies={}, s3={'addressing_style': 'path'}))


def upload_context(connection, account, identifier, fingerprint=None, bind=False):
    from file_versions import version_access
    revision = connection.execute('SELECT file_id FROM file_version WHERE id=%s AND company_id=%s AND uploader_id=%s', (identifier, account['company_id'], account['id'])).fetchone()
    if revision:
        parent = version_access(connection, account, revision['file_id'], edit=True)
        file = connection.execute('SELECT * FROM file_version WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
        if file['state'] == 'pending' and file['base_version'] != parent['current_version']:
            raise HTTPException(409, 'A newer version is available. Refresh version history before uploading again.')
    else:
        file = connection.execute("SELECT * FROM stored_file WHERE id=%s AND company_id=%s AND team_id=%s AND owner_id=%s AND upload_key IS NOT NULL FOR UPDATE", (identifier, account['company_id'], account['team_id'], account['id'])).fetchone()
    if not file:
        raise HTTPException(404, 'Upload not found.')
    policy = connection.execute('SELECT * FROM company_upload_policy WHERE company_id=%s AND enabled AND connection_id=%s FOR SHARE', (account['company_id'], file['connection_id'])).fetchone()
    storage = connection.execute('SELECT * FROM storage_connection WHERE id=%s AND enabled FOR SHARE', (file['connection_id'],)).fetchone()
    active = connection.execute("SELECT id FROM team_account WHERE id=%s AND company_id=%s AND team_id=%s AND status='active' FOR SHARE", (account['id'], account['company_id'], account['team_id'])).fetchone()
    if not policy or not storage or not active or file['size_bytes'] > min(policy['max_file_bytes'], MULTIPART_UPLOAD_BYTES):
        raise HTTPException(409, 'Uploads are disabled or the upload policy changed.')
    if file['state'] not in ('pending', 'ready'):
        raise HTTPException(409, 'Upload state changed.')
    if fingerprint is not None:
        if file['upload_fingerprint'] and file['upload_fingerprint'] != fingerprint:
            raise HTTPException(409, 'File contents differ from the original upload. Select the original file or cancel this upload.')
        if not file['upload_fingerprint'] and file['state'] == 'pending':
            if not bind or file['multipart_upload_id']:
                raise HTTPException(409, 'This upload has no verified content identity. Cancel it and start a new upload.')
            table = 'file_version' if 'file_id' in file else 'stored_file'
            connection.execute(f'UPDATE {table} SET upload_fingerprint=%s WHERE id=%s', (fingerprint, identifier))
            file['upload_fingerprint'] = fingerprint
    return file, storage


def mark_uploaded(connection, account, file, metadata, version=None):
    from automation import enqueue_upload
    from file_versions import initial_version, publish_version
    if 'file_id' in file:
        return publish_version(connection, account, file, metadata, version)
    connection.execute("UPDATE stored_file SET etag=%s,object_version=%s,state='ready',uploaded_at=clock_timestamp(),multipart_upload_id=NULL,multipart_part_bytes=NULL WHERE id=%s", (metadata['ETag'], version, file['id']))
    initial_version(connection, connection.execute('SELECT * FROM stored_file WHERE id=%s', (file['id'],)).fetchone())
    enqueue_upload(connection, file['id'], account)
    return {'id': file['id'], 'state': 'ready', 'detail': 'Upload complete.'}


def multipart_parts(client, storage, file):
    parts = []
    marker = 0
    while True:
        result = client.list_parts(Bucket=storage['bucket'], Key=file['object_key'], UploadId=file['multipart_upload_id'], PartNumberMarker=marker, MaxParts=1000)
        for part in result.get('Parts', []):
            number = part['PartNumber']
            expected = min(file['multipart_part_bytes'], file['size_bytes'] - (number - 1) * file['multipart_part_bytes'])
            if not marker < number <= MAX_PARTS or expected <= 0 or part['Size'] != expected or not part.get('ETag'):
                raise ValueError('Multipart metadata mismatch')
            if parts and number <= parts[-1]['PartNumber']:
                raise ValueError('Multipart order mismatch')
            parts.append(part)
        if not result.get('IsTruncated'):
            return parts
        next_marker = result.get('NextPartNumberMarker', 0)
        if not marker < next_marker <= MAX_PARTS:
            raise ValueError('Invalid multipart pagination')
        marker = next_marker


def recover_multipart(client, storage, file):
    try:
        metadata = client.head_object(Bucket=storage['bucket'], Key=file['object_key'])
    except ClientError as error:
        if error.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
            return None
        raise
    if metadata.get('ContentLength') != file['size_bytes'] or metadata.get('Metadata', {}).get('mikan-upload') != str(file['id']) or not metadata.get('ETag'):
        raise ValueError('Completed multipart metadata mismatch')
    return metadata


def transfer_account(request: Request, response: Response, database=Depends(transfer_database)):
    with database() as connection, connection.transaction():
        return current_team_account(request, response, connection)


def recheck_account(connection, request, account):
    current = current_team_account(request, Response(), connection)
    if any(current[key] != account[key] for key in ('id', 'company_id', 'team_id', 'role')):
        raise HTTPException(401, 'Account changed. Sign in again.')


@contextmanager
def transfer_operation(database, request, account, identifier):
    with database() as connection:
        key = f'upload-operation:{identifier}'
        locked = connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired', (key,)).fetchone()['acquired']
        if not locked:
            raise HTTPException(409, 'Another operation is active for this upload. Retry after it finishes.')
        try:
            with connection.transaction():
                recheck_account(connection, request, account)
            yield connection
        finally:
            connection.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))


def transfer_preflight(database, request, account, identifier, fingerprint=None, number=None):
    with database() as connection, connection.transaction():
        recheck_account(connection, request, account)
        file, _ = upload_context(connection, account, identifier, fingerprint)
        if number is None:
            if file['size_bytes'] > SINGLE_UPLOAD_BYTES:
                raise HTTPException(413, 'Single-request uploads cannot exceed 5 GB. Use multipart upload.')
            return file['size_bytes']
        if file['state'] != 'pending' or not file['multipart_upload_id']:
            raise HTTPException(409, 'Multipart upload is not active. Retry the upload.')
        expected = min(file['multipart_part_bytes'], file['size_bytes'] - (number - 1) * file['multipart_part_bytes'])
        if expected <= 0:
            raise HTTPException(422, 'Invalid upload part number.')
        return expected


def finish_upload(database, request, account, identifier, body, length):
    with transfer_operation(database, request, account, identifier) as connection:
        with connection.transaction():
            file, storage = upload_context(connection, account, identifier)
        if file['state'] == 'ready':
            return {'id': identifier, 'detail': 'Already uploaded.'}
        if file['multipart_upload_id'] or length != file['size_bytes']:
            raise HTTPException(409, 'Upload size or state changed. Select the original file.')
        if length > SINGLE_UPLOAD_BYTES:
            raise HTTPException(413, 'Single-request uploads cannot exceed 5 GB. Use multipart upload.')
        try:
            with closing(storage_client(storage)) as client:
                result = client.put_object(Bucket=storage['bucket'], Key=file['object_key'], Body=body, ContentLength=length, ContentType='application/octet-stream')
                version = result.get('VersionId') if storage['provider'] == 's3' else None
                metadata = client.head_object(Bucket=storage['bucket'], Key=file['object_key'], **({'VersionId': version} if version else {}))
                if metadata.get('ContentLength') != length or not result.get('ETag') or metadata.get('ETag') != result['ETag']:
                    raise ValueError('Upload metadata mismatch')
        except (BotoCoreError, ClientError, InvalidToken, ValueError, UnicodeError) as error:
            raise HTTPException(503, 'Upload could not be confirmed. Retry this upload; its quota reservation is retained.') from error
        with connection.transaction():
            recheck_account(connection, request, account)
            file, _ = upload_context(connection, account, identifier)
            return mark_uploaded(connection, account, file, metadata, version)


def send_part(database, request, account, identifier, number, body, length, fingerprint):
    with transfer_operation(database, request, account, identifier) as connection:
        with connection.transaction():
            file, storage = upload_context(connection, account, identifier, fingerprint)
        if file['state'] != 'pending' or not file['multipart_upload_id']:
            raise HTTPException(409, 'Multipart upload is not active. Retry the upload.')
        expected = min(file['multipart_part_bytes'], file['size_bytes'] - (number - 1) * file['multipart_part_bytes'])
        if expected <= 0 or length != expected:
            raise HTTPException(409, 'Upload part does not match the reserved size.')
        try:
            with closing(storage_client(storage)) as client:
                result = client.upload_part(Bucket=storage['bucket'], Key=file['object_key'], UploadId=file['multipart_upload_id'], PartNumber=number, Body=body, ContentLength=length)
                if not result.get('ETag'):
                    raise ValueError('Missing part confirmation')
        except (BotoCoreError, ClientError, InvalidToken, ValueError, UnicodeError) as error:
            raise HTTPException(503, 'Part upload interrupted. Retry the same file to resume.') from error
        with connection.transaction():
            recheck_account(connection, request, account)
            upload_context(connection, account, identifier, fingerprint)
        return {'part_number': number}


def publish_transfer(connection, request, account, identifier, fingerprint, metadata, version):
    with connection.transaction():
        recheck_account(connection, request, account)
        file, _ = upload_context(connection, account, identifier, fingerprint)
        return mark_uploaded(connection, account, file, metadata, version)


def cancellation_context(connection, account, identifier):
    file = connection.execute('''SELECT revision.* FROM file_version revision
        JOIN stored_file parent ON parent.id=revision.file_id
        WHERE revision.id=%s AND revision.company_id=%s AND (revision.uploader_id=%s OR parent.owner_id=%s)
        FOR UPDATE OF revision''', (identifier, account['company_id'], account['id'], account['id'])).fetchone()
    if not file:
        file = connection.execute('''SELECT * FROM stored_file WHERE id=%s AND company_id=%s AND owner_id=%s
            AND upload_key IS NOT NULL FOR UPDATE''', (identifier, account['company_id'], account['id'])).fetchone()
    if not file:
        raise HTTPException(404, 'Upload not found.')
    if file['state'] not in ('pending', 'cancelling', 'cancelled'):
        raise HTTPException(409, 'Completed files cannot be cancelled.')
    storage = connection.execute('SELECT * FROM storage_connection WHERE id=%s', (file['connection_id'],)).fetchone()
    return file, storage


def cleanup_upload(client, storage, file):
    bucket, key = storage['bucket'], file['object_key']
    if file['multipart_upload_id']:
        try:
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=file['multipart_upload_id'])
        except ClientError as error:
            if error.response.get('Error', {}).get('Code') != 'NoSuchUpload':
                raise
    for _attempt in range(100):
        result = client.list_multipart_uploads(Bucket=bucket, Prefix=key, MaxUploads=1000)
        uploads = [upload for upload in result.get('Uploads', []) if upload['Key'] == key]
        if not uploads:
            if result.get('IsTruncated'):
                raise ValueError('Incomplete upload inventory')
            break
        for upload in uploads:
            try:
                client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload['UploadId'])
            except ClientError as error:
                if error.response.get('Error', {}).get('Code') != 'NoSuchUpload':
                    raise
    else:
        raise ValueError('Upload cleanup limit reached')
    if storage['provider'] == 's3':
        for _attempt in range(100):
            result = client.list_object_versions(Bucket=bucket, Prefix=key, MaxKeys=1000)
            versions = [version for version in result.get('Versions', []) + result.get('DeleteMarkers', []) if version['Key'] == key]
            if not versions:
                if result.get('IsTruncated'):
                    raise ValueError('Incomplete object inventory')
                break
            for version in versions:
                client.delete_object(Bucket=bucket, Key=key, VersionId=version['VersionId'])
        else:
            raise ValueError('Object cleanup limit reached')
    else:
        client.delete_object(Bucket=bucket, Key=key)
    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
            return
        raise
    raise ValueError('Object cleanup not confirmed')


def create_upload_router(company_dependency, origin_dependency):
    router = APIRouter()
    mutation = [Depends(origin_dependency)]

    @router.get('/company/teams/workflows/uploads')
    def policy(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        return {'policy': connection.execute('SELECT enabled,max_file_bytes FROM company_upload_policy WHERE company_id=%s', (admin['company_id'],)).fetchone()}

    @router.post('/company/teams/workflows/uploads', dependencies=mutation)
    def save_policy(payload: UploadPolicy, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        with connection.transaction():
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f"upload-policy:{admin['company_id']}",))
            existing = connection.execute('SELECT connection_id FROM company_upload_policy WHERE company_id=%s FOR UPDATE', (admin['company_id'],)).fetchone()
            if existing:
                storage = connection.execute('SELECT id,enabled FROM storage_connection WHERE id=%s FOR SHARE', (existing['connection_id'],)).fetchone()
            else:
                storage = connection.execute('SELECT id,enabled FROM storage_connection WHERE enabled ORDER BY id LIMIT 1 FOR SHARE').fetchone()
            if not storage or (payload.enabled and not storage['enabled']):
                raise HTTPException(409, 'Storage is temporarily unavailable. Contact your platform administrator.')
            connection.execute('''INSERT INTO company_upload_policy(company_id,connection_id,enabled,max_file_bytes) VALUES (%s,%s,%s,%s)
                ON CONFLICT(company_id) DO UPDATE SET enabled=EXCLUDED.enabled,max_file_bytes=EXCLUDED.max_file_bytes,updated_at=clock_timestamp()''', (admin['company_id'], storage['id'], payload.enabled, payload.max_file_bytes))
        return {'detail': 'Upload settings saved.'}

    @router.get('/team/files')
    def files(connection=Depends(get_db, scope="function"), account=Depends(current_team_account), page: int = Query(1, ge=1, le=100000), search: str = Query('', max_length=100), from_date: date | None = None, to_date: date | None = None,
              folder: str | None = Query(None, max_length=255), owner_id: int | None = Query(None, gt=0, le=9223372036854775807)):
        dates, values = date_clause(from_date, to_date)
        scope = [account['company_id'], account['team_id'], account['id']]
        drive_owner = None
        if owner_id is not None:
            if account['role'] != 'manager' or not connection.execute(
                'SELECT 1 FROM team WHERE id=%s AND company_id=%s AND manager_can_view_drives',
                (account['team_id'], account['company_id'])).fetchone():
                raise HTTPException(403, 'Team drive access is not enabled.')
            drive_owner = connection.execute("SELECT id,name FROM team_account WHERE id=%s AND company_id=%s AND team_id=%s AND status='active'",
                (owner_id, account['company_id'], account['team_id'])).fetchone()
            if not drive_owner:
                raise HTTPException(404, 'Team member not found.')
            scope[2] = owner_id
        if folder is None:
            result = list_rows(connection, 'stored_file', 'id,name,folder,size_bytes,state,created_at', f"company_id=%s AND team_id=%s AND owner_id=%s AND state NOT IN ('trashed','purging','purged','cancelled') AND strpos(lower(name || ' ' || folder),lower(%s))>0 AND {dates}", [*scope, search, *values], page)
        else:
            try:
                validate_file_target(folder, 'move')
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            if folder and not connection.execute(f'SELECT 1 FROM {FOLDERS} WHERE company_id=%s AND team_id=%s AND owner_id=%s AND path=%s', (*scope, folder)).fetchone():
                raise HTTPException(404, 'Folder not found. It may have been moved or removed.')
            entries = f'''(SELECT company_id,team_id,owner_id,'folder:' || path AS id,'folder' AS kind,
                split_part(path,'/',-1) AS name,path,regexp_replace(path,'(^|/)[^/]+$','') AS folder,
                NULL::bigint AS size_bytes,NULL::text AS state,created_at FROM {FOLDERS}
                UNION ALL SELECT company_id,team_id,owner_id,id::text,'file',name,NULL::text,folder,size_bytes,state,created_at
                FROM stored_file WHERE state NOT IN ('trashed','purging','purged','cancelled')) entry'''
            result = list_rows(connection, entries, 'id,kind,name,path,folder,size_bytes,state,created_at',
                f"company_id=%s AND team_id=%s AND owner_id=%s AND folder=%s AND strpos(lower(name),lower(%s))>0 AND {dates}",
                [*scope, folder, search, *values], page, 'kind DESC,lower(name),id')
        result['policy'] = connection.execute('SELECT enabled,max_file_bytes FROM company_upload_policy WHERE company_id=%s', (account['company_id'],)).fetchone()
        if owner_id is not None:
            result['policy'] = None
            result['drive_owner'] = drive_owner
        return result

    @router.post('/team/files/folders', status_code=201, dependencies=mutation)
    def create_folder(payload: FolderInput, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        path = '/'.join(part for part in (payload.parent, payload.name) if part)
        if len(path) > 255:
            raise HTTPException(422, 'Folder path exceeds 255 characters.')
        scope = (account['company_id'], account['team_id'], account['id'])
        with connection.transaction():
            connection.execute('LOCK TABLE stored_file, data_folder IN SHARE ROW EXCLUSIVE MODE')
            if not connection.execute("SELECT id FROM team_account WHERE company_id=%s AND team_id=%s AND id=%s AND status='active' FOR SHARE", scope).fetchone():
                raise HTTPException(404, 'Drive not found.')
            if payload.parent and not connection.execute(f'SELECT 1 FROM {FOLDERS} WHERE company_id=%s AND team_id=%s AND owner_id=%s AND path=%s', (*scope, payload.parent)).fetchone():
                raise HTTPException(404, 'Parent folder not found. Refresh your drive.')
            if connection.execute(f'SELECT 1 FROM {FOLDERS} WHERE company_id=%s AND team_id=%s AND owner_id=%s AND path=%s', (*scope, path)).fetchone():
                raise HTTPException(409, 'Folder already exists.')
            connection.execute('INSERT INTO data_folder(company_id,team_id,owner_id,path) VALUES (%s,%s,%s,%s)', (*scope, path))
            connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
                (account['company_id'], account['name'], 'folder_create', path, f"Team {account['team_id']}, owner {account['id']}"))
        return {'path': path, 'detail': 'Folder created.'}

    @router.post('/team/files/uploads', status_code=201, dependencies=mutation)
    def reserve(payload: UploadInput, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        try:
            with connection.transaction():
                connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f"upload:{account['company_id']}:{account['id']}:{payload.upload_key}",))
                existing = connection.execute('SELECT * FROM stored_file WHERE company_id=%s AND owner_id=%s AND upload_key=%s', (account['company_id'], account['id'], payload.upload_key)).fetchone()
                if existing:
                    if existing['team_id'] != account['team_id'] or any(existing[key] != getattr(payload, key) for key in ('name','folder','size_bytes')):
                        raise HTTPException(409, 'Upload key already used for another file.')
                    return {'id': existing['id'], 'state': existing['state']}
                policy = connection.execute('''SELECT policy.* FROM company_upload_policy policy JOIN storage_connection storage ON storage.id=policy.connection_id
                    WHERE policy.company_id=%s AND policy.enabled AND storage.enabled FOR SHARE OF policy,storage''', (account['company_id'],)).fetchone()
                if not policy:
                    raise HTTPException(409, 'Your company admin must enable uploads first.')
                if payload.size_bytes > policy['max_file_bytes']:
                    raise HTTPException(413, 'File exceeds the company upload limit.')
                identifier = uuid4()
                connection.execute('''INSERT INTO stored_file(id,company_id,team_id,owner_id,connection_id,object_key,etag,name,folder,size_bytes,upload_key)
                    VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s)''', (identifier, account['company_id'], account['team_id'], account['id'], policy['connection_id'], f"mikan/{account['company_id']}/{account['id']}/{identifier}", payload.name, payload.folder, payload.size_bytes, payload.upload_key))
                return {'id': identifier, 'state': 'pending'}
        except CheckViolation as error:
            raise HTTPException(409, 'Team storage allocation exceeded or not configured.') from error

    @router.post('/team/files/{identifier}/cancel', dependencies=mutation)
    def cancel_upload(identifier: UUID, request: Request, database=Depends(transfer_database), account=Depends(transfer_account)):
        with transfer_operation(database, request, account, identifier) as connection:
            with connection.transaction():
                file, storage = cancellation_context(connection, account, identifier)
                if file['state'] == 'cancelled':
                    return {'id': identifier, 'state': 'cancelled'}
                table = 'file_version' if 'file_id' in file else 'stored_file'
                connection.execute(f"UPDATE {table} SET state='cancelling' WHERE id=%s", (identifier,))
            try:
                with closing(storage_client(storage)) as client:
                    cleanup_upload(client, storage, file)
            except (BotoCoreError, ClientError, InvalidToken, ValueError, UnicodeError, KeyError) as error:
                raise HTTPException(503, 'Cleanup could not be confirmed. Upload is stopped; quota is retained. Retry cancellation.') from error
            with connection.transaction():
                recheck_account(connection, request, account)
                cancellation_context(connection, account, identifier)
                quota = ',quota_bytes=0' if table == 'file_version' else ''
                connection.execute(f"UPDATE {table} SET state='cancelled',multipart_upload_id=NULL,multipart_part_bytes=NULL{quota} WHERE id=%s", (identifier,))
                connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
                    (account['company_id'], account['name'], 'upload_cancel', str(identifier), file['name']))
            return {'id': identifier, 'state': 'cancelled'}

    @router.post('/team/files/{identifier}/upload', dependencies=mutation)
    async def upload(identifier: UUID, request: Request, database=Depends(transfer_database), account=Depends(transfer_account)):
        if request.headers.get('content-type') != 'application/octet-stream':
            raise HTTPException(415, 'Binary file content required.')
        limit = await run_in_threadpool(transfer_preflight, database, request, account, identifier)
        length = 0
        with tempfile.TemporaryFile() as body:
            async for chunk in request.stream():
                length += len(chunk)
                if length > min(limit, SINGLE_UPLOAD_BYTES):
                    raise HTTPException(413, 'Upload exceeds the reserved file size.')
                await run_in_threadpool(body.write, chunk)
            body.seek(0)
            return await run_in_threadpool(finish_upload, database, request, account, identifier, body, length)

    @router.post('/team/files/{identifier}/multipart', dependencies=mutation)
    def start_multipart(identifier: UUID, request: Request, database=Depends(transfer_database), account=Depends(transfer_account), x_upload_fingerprint: str = Header(pattern='^[a-f0-9]{64}$')):
        with transfer_operation(database, request, account, identifier) as connection:
            with connection.transaction():
                file, storage = upload_context(connection, account, identifier, x_upload_fingerprint, bind=True)
            if file['state'] == 'ready':
                return {'id': identifier, 'state': 'ready'}
            if not file['size_bytes']:
                raise HTTPException(409, 'Empty files use a single-request upload.')
            try:
                with closing(storage_client(storage)) as client:
                    parts = []
                    if file['multipart_upload_id']:
                        try:
                            parts = multipart_parts(client, storage, file)
                        except ClientError as error:
                            if error.response.get('Error', {}).get('Code') != 'NoSuchUpload':
                                raise
                            metadata = recover_multipart(client, storage, file)
                            if metadata:
                                return publish_transfer(connection, request, account, identifier, x_upload_fingerprint, metadata, metadata.get('VersionId') if storage['provider'] == 's3' else None)
                            file['multipart_upload_id'] = None
                    if not file['multipart_upload_id']:
                        file['multipart_part_bytes'] = multipart_part_bytes(file['size_bytes'])
                        result = client.create_multipart_upload(Bucket=storage['bucket'], Key=file['object_key'], ContentType='application/octet-stream', Metadata={'mikan-upload': str(identifier)})
                        file['multipart_upload_id'] = result['UploadId']
                        table = 'file_version' if 'file_id' in file else 'stored_file'
                        with connection.transaction():
                            connection.execute(f'UPDATE {table} SET multipart_upload_id=%s,multipart_part_bytes=%s WHERE id=%s', (file['multipart_upload_id'], file['multipart_part_bytes'], identifier))
                    with connection.transaction():
                        recheck_account(connection, request, account)
                        upload_context(connection, account, identifier, x_upload_fingerprint)
                    return {'id': identifier, 'state': 'pending', 'part_bytes': file['multipart_part_bytes'], 'uploaded_parts': [part['PartNumber'] for part in parts]}
            except (BotoCoreError, ClientError, InvalidToken, ValueError, UnicodeError, KeyError) as error:
                raise HTTPException(503, 'Unable to resume upload. Retry the same file.') from error

    @router.post('/team/files/{identifier}/parts/{number}', dependencies=mutation)
    async def upload_part(identifier: UUID, request: Request, number: int = Path(ge=1, le=MAX_PARTS), database=Depends(transfer_database), account=Depends(transfer_account), x_upload_fingerprint: str = Header(pattern='^[a-f0-9]{64}$')):
        if request.headers.get('content-type') != 'application/octet-stream':
            raise HTTPException(415, 'Binary file content required.')
        limit = await run_in_threadpool(transfer_preflight, database, request, account, identifier, x_upload_fingerprint, number)
        length = 0
        with tempfile.TemporaryFile() as body:
            async for chunk in request.stream():
                length += len(chunk)
                if length > limit:
                    raise HTTPException(413, 'Upload part exceeds the reserved size.')
                await run_in_threadpool(body.write, chunk)
            body.seek(0)
            return await run_in_threadpool(send_part, database, request, account, identifier, number, body, length, x_upload_fingerprint)

    @router.post('/team/files/{identifier}/multipart/complete', dependencies=mutation)
    def complete_multipart(identifier: UUID, request: Request, database=Depends(transfer_database), account=Depends(transfer_account), x_upload_fingerprint: str = Header(pattern='^[a-f0-9]{64}$')):
        with transfer_operation(database, request, account, identifier) as connection:
            with connection.transaction():
                file, storage = upload_context(connection, account, identifier, x_upload_fingerprint)
            if file['state'] == 'ready':
                return {'id': identifier, 'state': 'ready', 'detail': 'Already uploaded.'}
            if not file['multipart_upload_id']:
                raise HTTPException(409, 'Multipart upload is not active.')
            try:
                with closing(storage_client(storage)) as client:
                    try:
                        parts = multipart_parts(client, storage, file)
                    except ClientError as error:
                        if error.response.get('Error', {}).get('Code') != 'NoSuchUpload':
                            raise
                        metadata = recover_multipart(client, storage, file)
                        if not metadata:
                            raise HTTPException(409, 'Multipart upload expired. Retry the same file.')
                        return publish_transfer(connection, request, account, identifier, x_upload_fingerprint, metadata, metadata.get('VersionId') if storage['provider'] == 's3' else None)
                    count = (file['size_bytes'] + file['multipart_part_bytes'] - 1) // file['multipart_part_bytes']
                    if [part['PartNumber'] for part in parts] != list(range(1, count + 1)):
                        raise HTTPException(409, 'Upload has missing parts. Retry the same file to resume.')
                    result = client.complete_multipart_upload(Bucket=storage['bucket'], Key=file['object_key'], UploadId=file['multipart_upload_id'], MultipartUpload={'Parts': [{'PartNumber': part['PartNumber'], 'ETag': part['ETag']} for part in parts]})
                    version = result.get('VersionId') if storage['provider'] == 's3' else None
                    metadata = client.head_object(Bucket=storage['bucket'], Key=file['object_key'], **({'VersionId': version} if version else {}))
                    if metadata.get('ContentLength') != file['size_bytes'] or not result.get('ETag') or metadata.get('ETag') != result['ETag'] or metadata.get('Metadata', {}).get('mikan-upload') != str(identifier):
                        raise ValueError('Multipart metadata mismatch')
                    return publish_transfer(connection, request, account, identifier, x_upload_fingerprint, metadata, version)
            except (BotoCoreError, ClientError, InvalidToken, ValueError, UnicodeError) as error:
                raise HTTPException(503, 'Upload could not be confirmed. Retry the same file to resume.') from error

    return router