import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import ForeignKeyViolation
from pydantic import BaseModel, ConfigDict, Field

from database import get_db, transfer_database
from trash import purge_file


class ClearSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company_id: int | None = Field(default=None, gt=0, le=9223372036854775807, strict=True)
    companies: bool = Field(default=False, strict=True)
    admins: bool = Field(default=False, strict=True)
    members: bool = Field(default=False, strict=True)
    files: bool = Field(default=False, strict=True)
    folders: bool = Field(default=False, strict=True)


class ClearInput(ClearSelection):
    confirmation: Literal['CLEAR DATA']
    fingerprint: str = Field(pattern='^[a-f0-9]{64}$')


CATEGORIES = ('companies', 'admins', 'members', 'files', 'folders')
SCOPE = '(%s::bigint IS NULL OR company_id=%s)'
TABLES = ('company', 'admin', 'team', 'team_account', 'stored_file', 'file_version',
          'data_folder', 'workflow', 'workflow_run', 'workflow_task', 'workflow_event',
          'workflow_notification', 'automation_job', 'notification', 'file_share',
          'file_activity', 'team_folder_activity', 'company_upload_policy',
          'company_trash_policy', 'data_activity')


def preview_clear(connection, selection):
    values = (selection.company_id, selection.company_id)
    if selection.company_id is not None and not connection.execute('SELECT id FROM company WHERE id=%s', (selection.company_id,)).fetchone():
        raise HTTPException(404, 'Company not found.')
    snapshots = {}
    sources = {
        'companies': ('company', 'id,name,updated_at', '(%s::bigint IS NULL OR id=%s)'),
        'admins': ('admin', 'id,name,email,company_id,is_active', SCOPE + " AND role='admin'"),
        'members': ('team_account', 'id,name,email,team_id,role,status', SCOPE),
        'files': ('stored_file', 'id,owner_id,team_id,connection_id,object_key,object_version,folder,name,size_bytes,current_version', SCOPE),
        'folders': ('data_folder', 'id,owner_id,team_id,path', SCOPE),
        'versions': ('file_version', 'id,file_id,uploader_id,connection_id,object_key,object_version,size_bytes', SCOPE),
        'teams': ('team', 'id,name', SCOPE),
        'workflows': ('workflow', 'id,version,updated_at', SCOPE),
        'workflow_runs': ('workflow_run', 'id,updated_at', SCOPE),
    }
    for key, (table, columns, where) in sources.items():
        snapshots[key] = connection.execute(f'SELECT {columns} FROM {table} WHERE {where} ORDER BY id', values).fetchall()
    counts = {key: len(rows) for key, rows in snapshots.items()}
    blockers = []
    if selection.companies:
        for key in ('admins', 'members', 'files', 'folders'):
            if counts[key] and not getattr(selection, key):
                blockers.append(f'Select {key} before clearing companies.')
    if selection.members:
        for key in ('files', 'folders'):
            if counts[key] and not getattr(selection, key):
                blockers.append(f'Select {key} before clearing members.')
    if selection.folders and not selection.files and any(row['folder'] for row in snapshots['files']):
        blockers.append('Select files before clearing folders containing files.')
    fingerprint = hashlib.sha256(json.dumps({'scope': selection.model_dump(include={'company_id', *CATEGORIES}), 'rows': snapshots}, default=str, sort_keys=True).encode()).hexdigest()
    remaining = connection.execute(f"SELECT count(*) AS total FROM stored_file WHERE {SCOPE} AND state<>'purged'", values).fetchone()['total']
    captured_ids = {row['id'] for row in snapshots['files']}
    pending = connection.execute(f"SELECT id FROM stored_file WHERE {SCOPE} AND state<>'purged' ORDER BY id", values).fetchall()
    next_file = next((row['id'] for row in pending if row['id'] in captured_ids), None)
    return {'counts': counts, 'blockers': blockers, 'fingerprint': fingerprint, 'remaining_files': remaining, 'next_file': next_file}


def clear_records(connection, selection):
    values = (selection.company_id, selection.company_id)
    run_scope = f'SELECT id FROM workflow_run WHERE {SCOPE}'
    if selection.files or selection.members or selection.companies:
        connection.execute(f'DELETE FROM automation_job WHERE {SCOPE}', values)
        connection.execute(f'DELETE FROM notification WHERE run_id IN ({run_scope})', values)
        for table in ('workflow_notification', 'workflow_event', 'workflow_task'):
            connection.execute(f'DELETE FROM {table} WHERE run_id IN ({run_scope})', values)
        connection.execute(f'DELETE FROM workflow_run WHERE {SCOPE}', values)
    if selection.files:
        for table in ('file_activity', 'file_share', 'file_version', 'stored_file'):
            connection.execute(f'DELETE FROM {table} WHERE {SCOPE}', values)
    if selection.folders:
        connection.execute(f'DELETE FROM data_folder WHERE {SCOPE}', values)
    if selection.members or selection.companies:
        connection.execute(f'DELETE FROM workflow_event WHERE workflow_id IN (SELECT id FROM workflow WHERE {SCOPE})', values)
        connection.execute(f'DELETE FROM workflow WHERE {SCOPE}', values)
    if selection.members:
        connection.execute(f'DELETE FROM team_account WHERE {SCOPE}', values)
    if selection.admins:
        connection.execute(f"DELETE FROM admin WHERE {SCOPE} AND role='admin'", values)
    if selection.companies:
        for table in ('notification', 'team_folder_activity', 'company_upload_policy', 'company_trash_policy', 'data_activity', 'team'):
            connection.execute(f'DELETE FROM {table} WHERE {SCOPE}', values)
        connection.execute('DELETE FROM company WHERE (%s::bigint IS NULL OR id=%s)', values)


def create_management_router(super_dependency, origin_dependency):
    router = APIRouter(dependencies=[Depends(super_dependency)])
    mutation = [Depends(origin_dependency)]

    @router.post('/admin/management/clear/preview', dependencies=mutation)
    def preview(payload: ClearSelection, connection=Depends(get_db, scope='function')):
        return preview_clear(connection, payload)

    @router.post('/admin/management/clear/execute', dependencies=mutation)
    def execute(payload: ClearInput, database=Depends(transfer_database)):
        if not any(getattr(payload, category) for category in CATEGORIES):
            raise HTTPException(422, 'Select at least one category.')
        with database() as connection:
            acquired = connection.execute("SELECT pg_try_advisory_lock(hashtextextended('super-admin-clear-data',0)) AS acquired").fetchone()['acquired']
            if not acquired:
                raise HTTPException(409, 'Another cleanup is running. Retry after it finishes.')
            try:
                with connection.transaction():
                    current = preview_clear(connection, payload)
                    if current['blockers']:
                        raise HTTPException(409, ' '.join(current['blockers']))
                    if current['fingerprint'] != payload.fingerprint:
                        raise HTTPException(409, 'Data changed since the preview. Review a new preview before continuing.')
                    identifier = current['next_file'] if payload.files else None
                if identifier:
                    def validate_claim():
                        claimed = preview_clear(connection, payload)
                        if claimed['fingerprint'] != payload.fingerprint or claimed['blockers']:
                            raise HTTPException(409, 'Data changed before file cleanup. Review a new preview before continuing.')

                    if not purge_file(connection, identifier, immediate=True, before_purge=validate_claim):
                        raise HTTPException(409, 'File cleanup could not be confirmed or an upload is active. Records are retained; review and retry.')
                    return {'done': False, 'remaining_files': max(0, current['remaining_files'] - 1)}
                with connection.transaction():
                    connection.execute('LOCK TABLE ' + ','.join(TABLES) + ' IN SHARE ROW EXCLUSIVE MODE')
                    current = preview_clear(connection, payload)
                    if current['fingerprint'] != payload.fingerprint or current['blockers'] or (payload.files and current['remaining_files']):
                        raise HTTPException(409, 'Data changed. Review a new preview before continuing.')
                    clear_records(connection, payload)
                return {'done': True, 'detail': 'Selected data cleared. Super-admin accounts and integrations preserved.'}
            except ForeignKeyViolation as error:
                raise HTTPException(409, 'Dependent records remain. Nothing in the final record-removal step was deleted; review the selected categories.') from error
            finally:
                connection.execute("SELECT pg_advisory_unlock(hashtextextended('super-admin-clear-data',0))")

    @router.delete('/companies/{identifier}', dependencies=mutation)
    def delete_company(identifier: int, connection=Depends(get_db, scope='function')):
        selection = ClearSelection(company_id=identifier, companies=True)
        with connection.transaction():
            connection.execute('LOCK TABLE ' + ','.join(TABLES) + ' IN SHARE ROW EXCLUSIVE MODE')
            current = preview_clear(connection, selection)
            if current['blockers']:
                raise HTTPException(409, 'This company has admins, members, files or folders. Use Clear Data to review and select its dependent data.')
            clear_records(connection, selection)
        return {'ok': True}

    return router