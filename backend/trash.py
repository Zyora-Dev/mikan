from contextlib import closing
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from database import get_db, transfer_connection
from files import record_file_activity
from teams import current_team_account
from uploads import cleanup_upload, storage_client
from workflows import StrictInput, date_clause, list_rows


class TrashPolicy(StrictInput):
    enabled: bool = Field(strict=True)
    retention_days: int = Field(ge=1, le=3650, strict=True)


def create_trash_router(company_dependency, origin_dependency):
    router = APIRouter()
    mutation = [Depends(origin_dependency)]

    @router.get('/company/teams/data/trash-settings')
    def settings(connection=Depends(get_db, scope='function'), admin=Depends(company_dependency)):
        return connection.execute('SELECT enabled,retention_days FROM company_trash_policy WHERE company_id=%s', (admin['company_id'],)).fetchone() or {'enabled': False, 'retention_days': 30}

    @router.post('/company/teams/data/trash-settings', dependencies=mutation)
    def save_settings(payload: TrashPolicy, connection=Depends(get_db, scope='function'), admin=Depends(company_dependency)):
        connection.execute('''INSERT INTO company_trash_policy(company_id,enabled,retention_days) VALUES (%s,%s,%s)
            ON CONFLICT(company_id) DO UPDATE SET enabled=EXCLUDED.enabled,retention_days=EXCLUDED.retention_days,updated_at=clock_timestamp()''',
            (admin['company_id'], payload.enabled, payload.retention_days))
        connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
            (admin['company_id'], admin['name'], 'trash_policy', 'Trash retention', f'Automatic cleanup: {payload.enabled}; retention: {payload.retention_days} days.'))
        return {'detail': 'Trash retention saved.'}

    @router.get('/team/files/trash')
    def trash_files(connection=Depends(get_db, scope='function'), account=Depends(current_team_account),
                    page: int = Query(1, ge=1, le=100000), search: str = Query('', max_length=100),
                    from_date: date | None = None, to_date: date | None = None):
        dates, values = date_clause(from_date, to_date, 'trashed_at')
        result = list_rows(connection, 'stored_file', "id,name,folder,size_bytes,state,created_at,trashed_at,trashed_by_owner,purge_error,'file' AS kind",
            f"company_id=%s AND owner_id=%s AND state IN ('trashed','purging') AND strpos(lower(name || ' ' || folder),lower(%s))>0 AND {dates}",
            [account['company_id'], account['id'], search, *values], page, 'trashed_at DESC,id DESC')
        result['retention'] = connection.execute('SELECT enabled,retention_days FROM company_trash_policy WHERE company_id=%s', (account['company_id'],)).fetchone()
        return result

    @router.post('/team/files/{identifier}/trash', dependencies=mutation)
    def move_to_trash(identifier: UUID, connection=Depends(get_db, scope='function'), account=Depends(current_team_account)):
        file = connection.execute('SELECT * FROM stored_file WHERE id=%s AND company_id=%s AND owner_id=%s FOR UPDATE',
            (identifier, account['company_id'], account['id'])).fetchone()
        if not file:
            raise HTTPException(404, 'File not found.')
        if file['state'] == 'trashed' and file['trashed_by_owner']:
            return {'detail': 'File is already in Trash.'}
        if file['state'] != 'ready':
            raise HTTPException(409, 'Only available files can be moved to Trash.')
        connection.execute("UPDATE stored_file SET state='trashed',trashed_by_owner=TRUE WHERE id=%s", (identifier,))
        record_file_activity(connection, account, identifier, 'file_trash', 'Owner moved file to Trash.')
        return {'detail': 'File moved to Trash.'}

    @router.post('/team/files/{identifier}/restore', dependencies=mutation)
    def restore(identifier: UUID, connection=Depends(get_db, scope='function'), account=Depends(current_team_account)):
        file = connection.execute('SELECT * FROM stored_file WHERE id=%s AND company_id=%s AND owner_id=%s FOR UPDATE',
            (identifier, account['company_id'], account['id'])).fetchone()
        if not file:
            raise HTTPException(404, 'File not found.')
        if file['state'] != 'trashed' or not file['trashed_by_owner'] or file['admin_trashed_at']:
            raise HTTPException(409, 'This file cannot be restored here. Contact your company administrator.')
        connection.execute("UPDATE stored_file SET state='ready' WHERE id=%s", (identifier,))
        record_file_activity(connection, account, identifier, 'file_restore', 'Owner restored file from Trash.')
        return {'detail': 'File restored.'}

    return router


def purge_file(connection, identifier, *, immediate=False, before_purge=None):
    locked = []
    try:
        with connection.transaction():
            file = connection.execute('''SELECT * FROM stored_file WHERE id=%s
                AND (%s OR purge_next_attempt_at IS NULL OR purge_next_attempt_at <= clock_timestamp()) FOR UPDATE''', (identifier, immediate)).fetchone()
            if not file or (not immediate and file['state'] not in ('trashed', 'purging')):
                return False
            if file['state'] == 'trashed' and not immediate:
                policy = connection.execute('''SELECT 1 FROM company_trash_policy WHERE company_id=%s AND enabled
                    AND %s::timestamptz + retention_days * INTERVAL '1 day' <= clock_timestamp() FOR SHARE''',
                    (file['company_id'], file['trashed_at'])).fetchone()
                if not policy:
                    return False
            versions = connection.execute('SELECT * FROM file_version WHERE file_id=%s ORDER BY id FOR UPDATE', (identifier,)).fetchall()
            for item in [file, *versions]:
                key = f"upload-operation:{item['id']}"
                if not connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired', (key,)).fetchone()['acquired']:
                    return False
                locked.append(key)
            if before_purge is not None:
                before_purge()
            connection.execute("UPDATE stored_file SET state=CASE WHEN state='cancelled' THEN state ELSE 'purging' END,purge_attempts=purge_attempts+1,purge_next_attempt_at=clock_timestamp()+INTERVAL '15 minutes' WHERE id=%s", (identifier,))
            objects = {(item['connection_id'], item['object_key']): item for item in [file, *versions]}
            stores = {store_id: connection.execute('SELECT * FROM storage_connection WHERE id=%s', (store_id,)).fetchone() for store_id, _key in objects}
        try:
            for (store_id, _key), item in objects.items():
                with closing(storage_client(stores[store_id])) as client:
                    cleanup_upload(client, stores[store_id], item)
        except Exception:
            with connection.transaction():
                connection.execute("UPDATE stored_file SET purge_error='Storage cleanup could not be confirmed. Automatic retry pending.' WHERE id=%s", (identifier,))
            return False
        with connection.transaction():
            connection.execute("SELECT id FROM stored_file WHERE id=%s FOR UPDATE", (identifier,))
            connection.execute("UPDATE file_version SET quota_bytes=0,state='cancelled',multipart_upload_id=NULL WHERE file_id=%s", (identifier,))
            connection.execute("UPDATE stored_file SET state='purged',purged_at=clock_timestamp(),purge_error=NULL,purge_next_attempt_at=NULL,multipart_upload_id=NULL WHERE id=%s", (identifier,))
            connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
                (file['company_id'], 'Super admin' if immediate else 'System', 'file_purged', str(identifier), 'Confirmed cleanup; all object versions removed and storage released.' if immediate else 'Trash retention expired; all object versions removed and storage released.'))
        return True
    finally:
        for key in reversed(locked):
            connection.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))


def cleanup_trash():
    with transfer_connection() as connection:
        rows = connection.execute('''SELECT file.id FROM stored_file file LEFT JOIN company_trash_policy policy ON policy.company_id=file.company_id
            WHERE (file.state='purging' OR (file.state='trashed' AND policy.enabled AND file.trashed_at + policy.retention_days * INTERVAL '1 day' <= clock_timestamp()))
            AND (file.purge_next_attempt_at IS NULL OR file.purge_next_attempt_at <= clock_timestamp())
            ORDER BY file.trashed_at,file.id LIMIT 10''').fetchall()
        for row in rows:
            purge_file(connection, row['id'])