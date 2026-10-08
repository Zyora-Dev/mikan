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

ROOT_ENTRIES = """(WITH folder_sources AS (
    SELECT company_id,path,created_at FROM data_folder
    UNION ALL SELECT company_id,folder,created_at FROM stored_file
        WHERE folder<>'' AND state NOT IN ('trashed','purging','purged','cancelled')
    UNION ALL SELECT company_id,path,created_at FROM company_root_entry WHERE kind='folder'
    UNION ALL SELECT company_id,parent,created_at FROM company_root_entry WHERE kind='file' AND parent<>''
), folders AS (
    SELECT company_id,array_to_string((string_to_array(path,'/'))[1:depth],'/') AS path,min(created_at) AS created_at
    FROM folder_sources CROSS JOIN LATERAL generate_series(1,array_length(string_to_array(path,'/'),1)) depth
    GROUP BY company_id,array_to_string((string_to_array(path,'/'))[1:depth],'/')
)
SELECT 'folder:' || path AS id,company_id,'folder' AS kind,
    regexp_replace(path,'(^|/)[^/]+$','') AS parent,split_part(path,'/',-1) AS name,
    path,0::bigint AS size_bytes,'ready' AS state,created_at FROM folders
UNION ALL SELECT id::text,company_id,'file',folder,name,
    CASE WHEN folder='' THEN name ELSE folder || '/' || name END,size_bytes,state,created_at
    FROM stored_file WHERE state NOT IN ('trashed','purging','purged','cancelled')
UNION ALL SELECT id::text,company_id,kind,parent,name,path,size_bytes,state,created_at
    FROM company_root_entry WHERE kind='file') entry"""


def require_parent(connection, company_id, parent):
    if parent and not connection.execute(f"SELECT id FROM {ROOT_ENTRIES} WHERE company_id=%s AND path=%s AND kind='folder'", (company_id, parent)).fetchone():
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
    if connection.execute(f'SELECT id FROM {ROOT_ENTRIES} WHERE company_id=%s AND path=%s', (company_id, path)).fetchone():
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
        return list_rows(connection, ROOT_ENTRIES, 'id,kind,parent,name,path,size_bytes,state,created_at',
            f'company_id=%s AND parent=%s AND strpos(lower(name),lower(%s))>0 AND {dates}',
            [admin['company_id'], folder, search, *values], page, 'kind DESC,lower(name),id')

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
            FROM (SELECT id,company_id,name,size_bytes,object_key,object_version,etag,connection_id,state FROM stored_file
                UNION ALL SELECT id,company_id,name,size_bytes,object_key,object_version,etag,connection_id,state
                FROM company_root_entry WHERE kind='file') entry
            JOIN storage_connection storage ON storage.id=entry.connection_id
            WHERE entry.id=%s AND entry.company_id=%s AND entry.state='ready' AND storage.enabled""",
            (identifier, admin['company_id'])).fetchone()
        if not record:
            raise HTTPException(404, 'File not found.')
        return stream_file(record)

    @router.get('/files/{identifier}/versions')
    def versions(identifier: UUID, connection=Depends(get_db, scope='function'), admin=Depends(company_dependency)):
        file = connection.execute("""SELECT id,name,size_bytes,source_modified_at,created_at,uploaded_at
            FROM company_root_entry WHERE id=%s AND company_id=%s AND kind='file' AND state='ready'""",
            (identifier, admin['company_id'])).fetchone()
        if not file:
            raise HTTPException(404, 'File not found.')
        history = connection.execute("""SELECT id,version_label,name,size_bytes,source_created_at,source_modified_at,
            created_at,uploaded_at,false AS current FROM company_root_version
            WHERE file_id=%s AND company_id=%s AND state='ready' ORDER BY source_modified_at DESC NULLS LAST,created_at DESC,id DESC""",
            (identifier, admin['company_id'])).fetchall()
        current = {
            'id': file['id'], 'version_label': 'Current', 'name': file['name'], 'size_bytes': file['size_bytes'],
            'source_created_at': None, 'source_modified_at': file['source_modified_at'], 'created_at': file['created_at'],
            'uploaded_at': file['uploaded_at'], 'current': True,
        }
        return {'items': [current, *history], 'total': len(history) + 1}

    @router.get('/files/{identifier}/versions/{version_id}/content')
    def version_content(identifier: UUID, version_id: UUID, connection=Depends(get_db, scope='function'), admin=Depends(company_dependency)):
        record = connection.execute("""SELECT version.name AS filename,version.size_bytes,version.object_key,
            version.object_version,version.etag,storage.* FROM company_root_version version
            JOIN company_root_entry file ON file.id=version.file_id AND file.company_id=version.company_id
                AND file.kind='file' AND file.state='ready'
            JOIN storage_connection storage ON storage.id=version.connection_id AND storage.enabled
            WHERE version.id=%s AND version.file_id=%s AND version.company_id=%s AND version.state='ready'""",
            (version_id, identifier, admin['company_id'])).fetchone()
        if not record:
            raise HTTPException(404, 'Version not found.')
        return stream_file(record)

    return router