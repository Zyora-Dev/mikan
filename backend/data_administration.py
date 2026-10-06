from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator

from database import get_db
from files import FOLDERS, stream_file
from workflows import AccountId, StrictInput, date_clause, list_rows, validate_file_target


class FileChange(StrictInput):
    action: Literal['edit', 'trash', 'restore']
    name: str = Field(min_length=1, max_length=255)
    folder: str = Field(max_length=255)
    expected_name: str = Field(min_length=1, max_length=255)
    expected_folder: str = Field(max_length=255)
    expected_state: Literal['ready', 'trashed']

    @field_validator('name', 'folder')
    @classmethod
    def clean(cls, value, info):
        validate_file_target(value, 'rename' if info.field_name == 'name' else 'move')
        return value


class FolderChange(StrictInput):
    action: Literal['create', 'rename', 'remove']
    team_id: AccountId
    owner_id: AccountId
    path: str = Field(min_length=1, max_length=255)
    target: str = Field(default='', max_length=255)

    @field_validator('path', 'target')
    @classmethod
    def clean(cls, value):
        validate_file_target(value, 'move')
        return value


def activity(connection, admin, action, subject, detail=''):
    connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
        (admin['company_id'], admin['name'], action, subject, detail))


def create_data_router(company_dependency, origin_dependency):
    router = APIRouter(prefix='/company/teams/data')

    @router.get('/files')
    def files(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), page: int = Query(1, ge=1, le=100000),
              search: str = Query('', max_length=100), team_id: int | None = Query(None, ge=1), owner_id: int | None = Query(None, ge=1),
              folder: str | None = Query(None, max_length=255), view: Literal['files', 'trash'] = 'files',
              from_date: date | None = None, to_date: date | None = None):
        dates, values = date_clause(from_date, to_date, 'file.trashed_at' if view == 'trash' else 'file.created_at')
        return list_rows(connection, 'stored_file file JOIN team ON team.id=file.team_id JOIN team_account owner ON owner.id=file.owner_id',
            "file.id,file.name,file.folder,file.size_bytes,file.state,file.created_at,file.trashed_at,file.purge_error,file.team_id,file.owner_id,team.name AS team_name,owner.name AS owner_name,(file.state='trashed' AND (file.admin_trashed_at IS NOT NULL OR file.trashed_by_owner)) AS restorable",
            f'''file.company_id=%s AND (%s::bigint IS NULL OR file.team_id=%s) AND (%s::bigint IS NULL OR file.owner_id=%s)
            AND (%s::text IS NULL OR file.folder=%s) AND (file.state IN ('trashed','purging'))=%s AND file.state NOT IN ('cancelled','purged')
            AND strpos(lower(file.name || ' ' || file.folder || ' ' || owner.name || ' ' || team.name),lower(%s))>0 AND {dates}''',
            [admin['company_id'],team_id,team_id,owner_id,owner_id,folder,folder,view=='trash',search,*values], page, 'file.created_at DESC,file.id DESC')

    @router.get('/folders')
    def folders(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), page: int = Query(1, ge=1, le=100000),
                search: str = Query('', max_length=100), from_date: date | None = None, to_date: date | None = None):
        dates, values = date_clause(from_date, to_date, 'folder.created_at')
        return list_rows(connection, FOLDERS + ' JOIN team ON team.id=folder.team_id JOIN team_account owner ON owner.id=folder.owner_id',
            'folder.path,folder.team_id,folder.owner_id,folder.created_at,team.name AS team_name,owner.name AS owner_name',
            f"folder.company_id=%s AND strpos(lower(folder.path || ' ' || owner.name || ' ' || team.name),lower(%s))>0 AND {dates}",
            [admin['company_id'],search,*values], page, 'folder.created_at DESC,folder.team_id,folder.owner_id,folder.path')

    @router.get('/activity')
    def events(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), page: int = Query(1, ge=1, le=100000),
               search: str = Query('', max_length=100), from_date: date | None = None, to_date: date | None = None):
        dates, values = date_clause(from_date, to_date)
        return list_rows(connection, 'data_activity', 'id,actor,action,subject,detail,created_at',
            f"company_id=%s AND strpos(lower(actor || ' ' || action || ' ' || subject || ' ' || detail),lower(%s))>0 AND {dates}", [admin['company_id'],search,*values], page)

    @router.get('/files/{identifier}/content')
    def download(identifier: UUID, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        record = connection.execute('''SELECT file.name AS filename,file.size_bytes,file.object_key,file.object_version,file.etag,storage.*
            FROM stored_file file JOIN storage_connection storage ON storage.id=file.connection_id
            WHERE file.id=%s AND file.company_id=%s AND file.state='ready' AND storage.enabled''', (identifier,admin['company_id'])).fetchone()
        if not record:
            raise HTTPException(404, 'File not found.')
        response = stream_file(record)
        activity(connection, admin, 'download_requested', record['filename'], str(identifier))
        return response

    @router.post('/files/{identifier}', dependencies=[Depends(origin_dependency)])
    def change_file(identifier: UUID, payload: FileChange, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        with connection.transaction():
            file = connection.execute('SELECT * FROM stored_file WHERE id=%s AND company_id=%s FOR UPDATE', (identifier,admin['company_id'])).fetchone()
            if not file:
                raise HTTPException(404, 'File not found.')
            if any(file[key] != getattr(payload, 'expected_' + key) for key in ('name','folder','state')):
                raise HTTPException(409, 'File changed. Refresh before trying again.')
            if payload.action == 'restore':
                if file['state'] != 'trashed' or not (file['admin_trashed_at'] or file['trashed_by_owner']):
                    raise HTTPException(409, 'This file cannot be restored here.')
                connection.execute("UPDATE stored_file SET state='ready',admin_trashed_at=NULL WHERE id=%s", (identifier,))
            elif file['state'] != 'ready':
                raise HTTPException(409, 'Only available files can be changed.')
            elif payload.action == 'trash':
                connection.execute("UPDATE stored_file SET state='trashed',admin_trashed_at=clock_timestamp() WHERE id=%s", (identifier,))
            else:
                connection.execute('UPDATE stored_file SET name=%s,folder=%s WHERE id=%s', (payload.name,payload.folder,identifier))
            activity(connection, admin, 'file_' + payload.action, file['name'], f"{identifier}: {file['folder']}/{file['name']} -> {payload.folder}/{payload.name}" if payload.action == 'edit' else str(identifier))
        return {'detail': 'File updated.'}

    @router.post('/folders', dependencies=[Depends(origin_dependency)])
    def change_folder(payload: FolderChange, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        with connection.transaction():
            connection.execute('LOCK TABLE stored_file, data_folder IN SHARE ROW EXCLUSIVE MODE')
            scope = (admin['company_id'],payload.team_id,payload.owner_id)
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'data-folders:{scope}',))
            if not connection.execute("SELECT id FROM team_account WHERE company_id=%s AND id=%s AND (%s<>'create' OR team_id=%s)",
                                      (admin['company_id'],payload.owner_id,payload.action,payload.team_id)).fetchone():
                raise HTTPException(404, 'Drive not found.')
            source = connection.execute(f'SELECT path FROM {FOLDERS} WHERE company_id=%s AND team_id=%s AND owner_id=%s AND path=%s', (*scope,payload.path)).fetchone()
            if payload.action == 'create':
                if source:
                    raise HTTPException(409, 'Folder already exists.')
                connection.execute('INSERT INTO data_folder(company_id,team_id,owner_id,path) VALUES (%s,%s,%s,%s)', (*scope,payload.path))
            else:
                if not source:
                    raise HTTPException(404, 'Folder not found.')
                prefix = payload.path + '/'
                affected = connection.execute('''SELECT id,state,folder FROM stored_file WHERE company_id=%s AND team_id=%s AND owner_id=%s
                    AND (folder=%s OR starts_with(folder,%s)) AND state NOT IN ('cancelled','purged') ORDER BY id FOR UPDATE''', (*scope,payload.path,prefix)).fetchall()
                nested = connection.execute('''SELECT id,path FROM data_folder WHERE company_id=%s AND team_id=%s AND owner_id=%s
                    AND (path=%s OR starts_with(path,%s)) ORDER BY id FOR UPDATE''', (*scope,payload.path,prefix)).fetchall()
                if payload.action == 'remove':
                    if affected or any(row['path'] != payload.path for row in nested):
                        raise HTTPException(409, 'Folder is not empty, including trash and subfolders.')
                    connection.execute('DELETE FROM data_folder WHERE company_id=%s AND team_id=%s AND owner_id=%s AND path=%s', (*scope,payload.path))
                else:
                    if not payload.target or payload.target == payload.path or payload.target.startswith(prefix):
                        raise HTTPException(422, 'Choose a different folder path outside this folder.')
                    if connection.execute(f'SELECT path FROM {FOLDERS} WHERE company_id=%s AND team_id=%s AND owner_id=%s AND path=%s', (*scope,payload.target)).fetchone():
                        raise HTTPException(409, 'Destination folder already exists.')
                    if any(row['state'] not in ('ready','trashed') for row in affected):
                        raise HTTPException(409, 'Folder contains pending or quarantined files.')
                    if any(len(payload.target + row['folder'][len(payload.path):]) > 255 for row in affected) or any(len(payload.target + row['path'][len(payload.path):]) > 255 for row in nested):
                        raise HTTPException(422, 'Resulting folder path is too long.')
                    for row in affected:
                        connection.execute('UPDATE stored_file SET folder=%s WHERE id=%s', (payload.target + row['folder'][len(payload.path):],row['id']))
                    for row in nested:
                        connection.execute('UPDATE data_folder SET path=%s WHERE id=%s', (payload.target + row['path'][len(payload.path):],row['id']))
                    connection.execute('INSERT INTO data_folder(company_id,team_id,owner_id,path) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING', (*scope,payload.target))
            activity(connection, admin, 'folder_' + payload.action, payload.path, f'Team {payload.team_id}, owner {payload.owner_id}' + (f' -> {payload.target}' if payload.action == 'rename' else ''))
        return {'detail': 'Folder updated.'}

    return router