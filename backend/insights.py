import csv
import io
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from database import get_db
from files import PRIVATE_HEADERS
from workflows import date_clause


AUDIT = '''SELECT 'data:' || id AS id, company_id, 'data' AS source, actor,
    action, subject, detail, created_at FROM data_activity
    UNION ALL
    SELECT 'folder:' || activity.id, activity.company_id, 'team_folders',
    coalesce(activity.actor_name, 'Not recorded'), activity.kind, team.name,
    concat('Quota: ', activity.previous_quota_bytes, ' -> ', activity.quota_bytes,
           '; Manager access: ', activity.previous_manager_access, ' -> ', activity.manager_access), activity.created_at
    FROM team_folder_activity activity JOIN team ON team.id=activity.team_id
    UNION ALL
    SELECT 'workflow:' || event.id, workflow.company_id, 'workflows', event.actor_name,
    event.kind, workflow.name, event.detail, event.created_at
    FROM workflow_event event JOIN workflow ON workflow.id=event.workflow_id'''

REPORTS = {
    'storage-teams': ('''SELECT team.id::text AS id, team.company_id, team.name,
        team.storage_quota_bytes AS quota_bytes, team.storage_used_bytes AS used_bytes,
        count(file.id) AS files, coalesce(sum(file.size_bytes) FILTER (WHERE file.state IN ('trashed','purging')),0) AS trash_bytes
        FROM team LEFT JOIN stored_file file ON file.team_id=team.id AND file.company_id=team.company_id AND file.state NOT IN ('cancelled','purged')
        GROUP BY team.id''', ['name', 'quota_bytes', 'used_bytes', 'files', 'trash_bytes']),
    'storage-employees': ('''SELECT owner.id || ':' || drive.team_id AS id, owner.company_id, owner.name,
        team.name AS team_name, coalesce(sum(file.size_bytes),0) +
        (SELECT coalesce(sum(version.quota_bytes),0) FROM file_version version
         JOIN stored_file original ON original.id=version.file_id AND original.company_id=version.company_id
         WHERE original.owner_id=owner.id AND version.company_id=owner.company_id AND version.team_id=drive.team_id) AS used_bytes,
        count(file.id) AS files,
        coalesce(sum(file.size_bytes) FILTER (WHERE file.state IN ('trashed','purging')),0) AS trash_bytes
        FROM team_account owner JOIN (
            SELECT id AS owner_id,team_id FROM team_account
            UNION SELECT owner_id,team_id FROM stored_file WHERE state NOT IN ('cancelled','purged')
            UNION SELECT original.owner_id,version.team_id FROM file_version version
                JOIN stored_file original ON original.id=version.file_id WHERE version.quota_bytes>0
        ) drive ON drive.owner_id=owner.id
        JOIN team ON team.id=drive.team_id AND team.company_id=owner.company_id
        LEFT JOIN stored_file file ON file.owner_id=owner.id AND file.team_id=drive.team_id AND file.company_id=owner.company_id AND file.state NOT IN ('cancelled','purged')
        GROUP BY owner.id,drive.team_id,team.name''', ['name', 'team_name', 'used_bytes', 'files', 'trash_bytes']),
    'largest-files': ('''SELECT file.id::text AS id,file.company_id,file.name,
        team.name AS team_name,owner.name AS owner_name,file.size_bytes,file.state,file.created_at
        FROM stored_file file JOIN team ON team.id=file.team_id AND team.company_id=file.company_id
        JOIN team_account owner ON owner.id=file.owner_id AND owner.company_id=file.company_id
        WHERE file.state NOT IN ('cancelled','purged')''',
        ['name', 'team_name', 'owner_name', 'size_bytes', 'state', 'created_at']),
    'workflows': ('''SELECT run.id::text AS id,run.company_id,run.workflow_name AS name,
        run.file_name,owner.name AS submitted_by,run.status,run.created_at,
        (SELECT count(*) FROM workflow_task WHERE run_id=run.id AND status='approved') AS approvals,
        (SELECT count(*) FROM workflow_task WHERE run_id=run.id AND status='pending') AS pending_reviews
        FROM workflow_run run JOIN team_account owner ON owner.id=run.submitter_id''',
        ['name', 'file_name', 'submitted_by', 'status', 'approvals', 'pending_reviews', 'created_at']),
    'automation': ('''SELECT job.id::text AS id,job.company_id,workflow.name,
        job.kind,job.status,job.attempts,job.created_at,job.updated_at
        FROM automation_job job JOIN workflow ON workflow.id=job.workflow_id''',
        ['name', 'kind', 'status', 'attempts', 'created_at', 'updated_at']),
}
Report = Literal['storage-teams', 'storage-employees', 'largest-files', 'activity', 'workflows', 'automation']
Source = Literal['all', 'data', 'team_folders', 'workflows']
EXPORT_LIMIT = 5000
COMPANY_STORAGE_CAPACITY_BYTES = 16_000_000_000_000


def csv_cell(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n'))):
        return "'" + value
    return value


def result(connection, dataset, columns, where, values, page, export, name, order):
    table = f'({dataset}) record JOIN company ON company.id=record.company_id'
    selected = ['record.id', 'record.company_id', 'company.name AS company_name', *['record.' + column for column in columns]]
    limit = EXPORT_LIMIT + 1 if export else 10
    rows = connection.execute(f"SELECT {','.join(selected)} FROM {table} WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s",
                              [*values, limit, 0 if export else (page - 1) * 10]).fetchall()
    if export:
        if len(rows) > EXPORT_LIMIT:
            raise HTTPException(413, f'Export exceeds {EXPORT_LIMIT} rows. Narrow the filters.')
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        fields = ['id', 'company_id', 'company_name', *columns]
        writer.writerow(fields)
        writer.writerows([csv_cell(row[field]) for field in fields] for row in rows)
        return Response('\ufeff' + output.getvalue(), media_type='text/csv',
                        headers={**PRIVATE_HEADERS, 'Content-Disposition': f'attachment; filename="mikan-{name}.csv"'})
    total = connection.execute(f'SELECT count(*) AS total FROM {table} WHERE {where}', values).fetchone()['total']
    return {'items': rows, 'total': total, 'generated_at': datetime.now(timezone.utc)}


def create_insights_router(admin_dependency, company_scope):
    router = APIRouter(prefix='/company/teams/insights' if company_scope else '/admin/insights')

    def scope(admin, company_id):
        if company_scope:
            if company_id is not None and company_id != admin['company_id']:
                raise HTTPException(403, 'Company scope cannot be changed.')
            return admin['company_id']
        return company_id

    @router.get('/storage')
    def storage(response: Response, connection=Depends(get_db, scope="function"), admin=Depends(admin_dependency),
                company_id: int | None = Query(None, ge=1, le=9223372036854775807)):
        if not company_scope:
            raise HTTPException(404, 'Not found.')
        company = scope(admin, company_id)
        response.headers.update(PRIVATE_HEADERS)
        totals = connection.execute('''SELECT coalesce(sum(storage_used_bytes),0) AS used_bytes,count(*) AS teams
            FROM team WHERE company_id=%s''', (company,)).fetchone()
        previews = {}
        for name, amount in [('storage-teams', 'used_bytes'), ('storage-employees', 'used_bytes'), ('largest-files', 'size_bytes')]:
            dataset, columns = REPORTS[name]
            selected = ','.join(['id', *columns])
            previews[name] = connection.execute(f'''SELECT {selected} FROM ({dataset}) record
                WHERE company_id=%s ORDER BY {amount} DESC,name,id LIMIT 5''', (company,)).fetchall()
        return {**totals, 'capacity_bytes': COMPANY_STORAGE_CAPACITY_BYTES,
            'remaining_bytes': max(0, COMPANY_STORAGE_CAPACITY_BYTES - totals['used_bytes']),
                'previews': previews, 'generated_at': datetime.now(timezone.utc)}

    @router.get('/audit')
    def audit(connection=Depends(get_db, scope="function"), admin=Depends(admin_dependency), page: int = Query(1, ge=1, le=100000),
              company_id: int | None = Query(None, ge=1, le=9223372036854775807), search: str = Query('', max_length=100),
              source: Source = 'all', action: str = Query('', max_length=100), from_date: date | None = None,
              to_date: date | None = None, export: bool = False):
        company = scope(admin, company_id)
        dates, values = date_clause(from_date, to_date, 'record.created_at')
        where = f'''(%s::bigint IS NULL OR record.company_id=%s) AND (%s='all' OR record.source=%s)
            AND (%s='' OR record.action=%s) AND {dates}
            AND strpos(lower(concat_ws(' ',company.name,record.actor,record.action,record.subject,record.detail)),lower(%s))>0'''
        return result(connection, AUDIT, ['source', 'actor', 'action', 'subject', 'detail', 'created_at'], where,
                      [company, company, source, source, action, action, *values, search], page, export, 'audit', 'record.created_at DESC,record.id DESC')

    @router.get('/reports/{report}')
    def report(report: Report, connection=Depends(get_db, scope="function"), admin=Depends(admin_dependency), page: int = Query(1, ge=1, le=100000),
               company_id: int | None = Query(None, ge=1, le=9223372036854775807), search: str = Query('', max_length=100),
               from_date: date | None = None, to_date: date | None = None, export: bool = False,
               sort: Literal['default', 'usage'] = 'default'):
        company = scope(admin, company_id)
        current = report.startswith('storage-')
        if current and (from_date or to_date):
            raise HTTPException(422, 'Storage reports are current snapshots, not historical usage.')
        dates, values = ('TRUE', []) if current else date_clause(from_date, to_date, 'record.created_at')
        if report == 'activity':
            dataset = f"SELECT * FROM ({AUDIT}) activity WHERE source IN ('data','team_folders') OR (source='workflows' AND action IN ('file_move','file_rename'))"
            columns = ['source', 'actor', 'action', 'subject', 'detail', 'created_at']
            searchable = 'record.actor,record.action,record.subject,record.detail'
        else:
            dataset, columns = REPORTS[report]
            searchable = ','.join('record.' + column for column in columns)
        where = f"(%s::bigint IS NULL OR record.company_id=%s) AND {dates} AND strpos(lower(concat_ws(' ',company.name,{searchable})),lower(%s))>0"
        return result(connection, dataset, columns, where, [company, company, *values, search], page, export, report,
                      'record.size_bytes DESC,record.name,record.id' if report == 'largest-files' else
                      'record.used_bytes DESC,record.name,record.id' if current and sort == 'usage' else
                      'company.name,record.name,record.id' if current else 'record.created_at DESC,record.id DESC')

    return router