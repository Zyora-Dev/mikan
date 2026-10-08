from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator

from database import get_db
from files import stream_file
from workflows import StrictInput, date_clause, list_rows, validate_file_target


class RootFolderInput(StrictInput):
    name: str = Field(min_length=1, max_length=255)
    parent: str = Field(default='', max_length=255)

    @field_validator('name', 'parent')
    @classmethod
    def clean_target(cls, value, info):
        validate_file_target(value, 'rename' if info.field_name == 'name' else 'move')
        return value


def root_path(parent, name):
    path = '/'.join(part for part in (parent, name) if part)
    if len(path) > 255:
        raise ValueError('Folder path exceeds 255 characters.')
    return path


def lock_root(connection, company_id):
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'company-root:{company_id}',))


def require_parent(connection, company_id, parent):
    if parent and not connection.execute("SELECT id FROM company_root_entry WHERE company_id=%s AND path=%s AND kind='folder'", (company_id, parent)).fetchone():
        raise HTTPException(404, 'Parent folder not found.')


def create_root_folder(connection, company_id, payload, source_id=None):
    path = root_path(payload.parent, payload.name)
    lock_root(connection, company_id)
    require_parent(connection, company_id, payload.parent)
    existing = connection.execute('SELECT * FROM company_root_entry WHERE company_id=%s AND path=%s', (company_id, path)).fetchone()
    if existing:
        if source_id and existing['kind'] == 'folder' and existing['source_id'] == source_id:
            return existing
        raise HTTPException(409, 'Destination already exists; nothing overwritten.')
    return connection.execute("""INSERT INTO company_root_entry(company_id,kind,parent,name,source_id)
        VALUES (%s,'folder',%s,%s,%s) RETURNING *""", (company_id, payload.parent, payload.name, source_id)).fetchone()


def create_company_root_router(company_dependency, origin_dependency):
    router = APIRouter(prefix='/company/teams/data/root')

    @router.get('')
    def browse(connection=Depends(get_db, scope='function'), admin=Depends(company_dependency),
               folder: str = Query('', max_length=255), search: str = Query('', max_length=100),
               page: int = Query(1, ge=1, le=100000), from_date: date | None = None, to_date: date | None = None):
        try:
            validate_file_target(folder, 'move')
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        require_parent(connection, admin['company_id'], folder)
        dates, values = date_clause(from_date, to_date)
        return list_rows(connection, 'company_root_entry', 'id,kind,parent,name,path,size_bytes,state,created_at',
            f'company_id=%s AND parent=%s AND strpos(lower(name),lower(%s))>0 AND {dates}',
            [admin['company_id'], folder, search, *values], page)

    @router.post('/folders', status_code=201, dependencies=[Depends(origin_dependency)])
    def folder(payload: RootFolderInput, connection=Depends(get_db, scope='function'), admin=Depends(company_dependency)):
        try:
            with connection.transaction():
                entry = create_root_folder(connection, admin['company_id'], payload)
                connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
                    (admin['company_id'], admin['name'], 'root_folder_create', entry['path'], 'Company root'))
            return {'path': entry['path']}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/files/{identifier}/content')
    def download(identifier: UUID, connection=Depends(get_db, scope='function'), admin=Depends(company_dependency)):
        record = connection.execute("""SELECT entry.name AS filename,entry.size_bytes,entry.object_key,entry.object_version,entry.etag,storage.*
            FROM company_root_entry entry JOIN storage_connection storage ON storage.id=entry.connection_id
            WHERE entry.id=%s AND entry.company_id=%s AND entry.kind='file' AND entry.state='ready' AND storage.enabled""",
            (identifier, admin['company_id'])).fetchone()
        if not record:
            raise HTTPException(404, 'File not found.')
        return stream_file(record)

    return router