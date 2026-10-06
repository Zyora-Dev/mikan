import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

import certifi
import urllib3
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb

from database import connect, get_db
from workflows import FILE_SNAPSHOT_SQL, Graph, Note, advance, date_clause, get_flow, list_rows, start_run, workflow_event
from zeptomail import send_email

logger = logging.getLogger(__name__)


def webhook_destinations(company):
    try:
        configured = json.loads(os.environ.get('MIKAN_WEBHOOKS', '{}'))
        destinations = configured.get(str(company), {})
        return destinations if isinstance(destinations, dict) else {}
    except (ValueError, AttributeError):
        return {}


def enqueue(connection, flow, kind, key, payload, file_id, run_id=None):
    connection.execute('''INSERT INTO automation_job(company_id,workflow_id,kind,dedupe_key,payload,file_id,run_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(dedupe_key) DO NOTHING''', (flow['company_id'], flow.get('workflow_id', flow.get('id')), kind, key, Jsonb(payload), file_id, run_id))


def enqueue_upload(connection, identifier, account):
    file = connection.execute('SELECT * FROM stored_file WHERE id=%s', (identifier,)).fetchone()
    flows = connection.execute('SELECT * FROM workflow WHERE company_id=%s AND enabled AND %s=ANY(team_ids) AND %s=ANY(submitter_roles) ORDER BY id', (account['company_id'], account['team_id'], account['role'])).fetchall()
    for flow in flows:
        trigger = next(node for node in Graph.model_validate(flow['graph']).nodes if node.type == 'trigger')
        if trigger.data.trigger == 'upload' and matches(file, trigger):
            enqueue(connection, flow, 'trigger', f"upload:{flow['id']}:{identifier}", {'version': flow['version'], 'trigger': 'upload'}, identifier)


def matches(file, trigger):
    return (not trigger.data.file_suffix or file['name'].lower().endswith(trigger.data.file_suffix.lower())) and (not trigger.data.folder or file['folder'] == trigger.data.folder)


def enqueue_delivery(connection, run, node, recipients):
    if node.data.channel == 'webhook':
        enqueue(connection, run, 'webhook', f"delivery:{run['id']}:{node.id}", {'node': node.id, 'destination': node.data.webhook_name, 'message': node.data.message}, run['file_id'], run['id'])
        return
    people = connection.execute("SELECT id FROM team_account WHERE id=ANY(%s) AND company_id=%s AND status='active'", (list(set(recipients)), run['company_id'])).fetchall()
    if not people:
        raise HTTPException(409, 'No active email recipients remain.')
    for person in people:
        enqueue(connection, run, 'email', f"delivery:{run['id']}:{node.id}:{person['id']}", {'node': node.id, 'recipient': person['id'], 'message': node.data.message}, run['file_id'], run['id'])


def enqueue_schedules(connection, now=None):
    now = now or datetime.now(timezone.utc)
    flows = connection.execute("SELECT * FROM workflow WHERE enabled AND EXISTS (SELECT 1 FROM jsonb_array_elements(graph->'nodes') node WHERE node->>'type'='trigger' AND node->'data'->>'trigger'='schedule') ORDER BY id").fetchall()
    for flow in flows:
        trigger = next(node for node in Graph.model_validate(flow['graph']).nodes if node.type == 'trigger')
        slot = int(now.timestamp()) // (trigger.data.interval_minutes * 60)
        prefix = f"schedule:{flow['id']}:{flow['version']}:{slot}:"
        files = connection.execute('''SELECT file.* FROM stored_file file JOIN team_account owner ON owner.id=file.owner_id AND owner.company_id=file.company_id AND owner.team_id=file.team_id
            JOIN storage_connection storage ON storage.id=file.connection_id
            WHERE file.company_id=%s AND file.team_id=ANY(%s) AND owner.role=ANY(%s) AND owner.status='active' AND file.state='ready' AND storage.enabled
            AND (%s='' OR file.folder=%s) AND (%s='' OR right(lower(file.name),length(%s))=lower(%s))
            AND NOT EXISTS (SELECT 1 FROM automation_job job WHERE job.dedupe_key=%s || file.id::text)
            ORDER BY file.created_at,file.id LIMIT 100''', (flow['company_id'], flow['team_ids'], flow['submitter_roles'], trigger.data.folder, trigger.data.folder, trigger.data.file_suffix, trigger.data.file_suffix, trigger.data.file_suffix, prefix)).fetchall()
        for file in files:
            enqueue(connection, flow, 'trigger', prefix + str(file['id']), {'version': flow['version'], 'trigger': 'schedule'}, file['id'])


def post_webhook(company, name, payload, identifier):
    destination = webhook_destinations(company).get(name)
    if not isinstance(destination, dict):
        raise HTTPException(409, 'Webhook destination is no longer configured.')
    url = urlsplit(destination.get('url', ''))
    secret = destination.get('secret', '')
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment or url.port not in (None, 443) or not isinstance(secret, str) or len(secret) < 32:
        raise HTTPException(409, 'Webhook requires a public HTTPS destination and a signing secret of at least 32 characters.')
    addresses = {record[4][0] for record in socket.getaddrinfo(url.hostname, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise HTTPException(409, 'Webhook destination must resolve only to public addresses.')
    body = json.dumps(payload, separators=(',', ':'), default=str).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    with urllib3.HTTPSConnectionPool(next(iter(addresses)), port=443, server_hostname=url.hostname, assert_hostname=url.hostname, cert_reqs='CERT_REQUIRED', ca_certs=certifi.where(), timeout=urllib3.Timeout(connect=3, read=10), retries=False) as pool:
        response = pool.urlopen('POST', (url.path or '/') + ('?' + url.query if url.query else ''), body=body,
            headers={'Host': url.hostname, 'Content-Type': 'application/json', 'Idempotency-Key': str(identifier), 'X-Mikan-Signature': 'sha256=' + signature}, redirect=False, preload_content=False)
        try:
            if not 200 <= response.status < 300:
                raise HTTPException(502, 'Webhook destination did not accept the event.')
        finally:
            response.close()


def execute_job(connection, job):
    if job['kind'] == 'trigger':
        flow = get_flow(connection, job['workflow_id'], job['company_id'], lock=True)
        if not flow['enabled']:
            raise HTTPException(409, 'Workflow is paused.')
        if flow['version'] != job['payload']['version']:
            return 'cancelled', 'Workflow definition changed before execution.'
        file = connection.execute(f"SELECT file.*, {FILE_SNAPSHOT_SQL} AS snapshot FROM stored_file file JOIN storage_connection storage ON storage.id=file.connection_id WHERE file.id=%s AND file.company_id=%s AND file.state='ready' AND storage.enabled FOR UPDATE OF file", (job['file_id'], job['company_id'])).fetchone()
        if not file:
            raise HTTPException(409, 'File is no longer ready or storage is disabled.')
        owner = connection.execute("SELECT * FROM team_account WHERE id=%s AND company_id=%s AND team_id=%s AND status='active'", (file['owner_id'], file['company_id'], file['team_id'])).fetchone()
        if not owner or owner['team_id'] not in flow['team_ids'] or owner['role'] not in flow['submitter_roles']:
            return 'cancelled', 'File owner is no longer eligible.'
        if connection.execute("SELECT id FROM workflow_run WHERE workflow_id=%s AND file_id=%s AND status='pending'", (flow['id'], file['id'])).fetchone():
            return 'cancelled', 'A request for this file is already pending.'
        result = start_run(connection, flow, file, owner, {}, job['id'])
        connection.execute('UPDATE automation_job SET run_id=%s WHERE id=%s', (result['id'], job['id']))
        return 'done', ''
    run = connection.execute('SELECT * FROM workflow_run WHERE id=%s AND company_id=%s FOR UPDATE', (job['run_id'], job['company_id'])).fetchone()
    flow = get_flow(connection, job['workflow_id'], job['company_id'], lock=True)
    if not run or run['status'] != 'pending' or run['current_node'] != job['payload']['node']:
        return 'cancelled', 'Request no longer needs this action.'
    if not flow['enabled']:
        raise HTTPException(409, 'Workflow is paused.')
    file = connection.execute(f"SELECT file.id FROM stored_file file JOIN storage_connection storage ON storage.id=file.connection_id JOIN team_account owner ON owner.id=file.owner_id AND owner.team_id=file.team_id AND owner.company_id=file.company_id WHERE file.id=%s AND file.company_id=%s AND file.state='ready' AND storage.enabled AND owner.status='active' AND {FILE_SNAPSHOT_SQL}=%s FOR SHARE OF file,owner,storage", (run['file_id'], run['company_id'], Jsonb(run['file_snapshot']))).fetchone()
    if not file:
        raise HTTPException(409, 'File, owner or storage changed before delivery.')
    if job['kind'] == 'email':
        person = connection.execute("SELECT email FROM team_account WHERE id=%s AND company_id=%s AND status='active' FOR SHARE", (job['payload']['recipient'], job['company_id'])).fetchone()
        settings = connection.execute('SELECT * FROM zeptomail_integration WHERE id=1 AND enabled').fetchone()
        if not person or not settings:
            raise HTTPException(409, 'Email integration or recipient is unavailable.')
        send_email(settings, person['email'], run['workflow_name'], job['payload']['message'])
    else:
        post_webhook(job['company_id'], job['payload']['destination'], {'event_id': job['id'], 'run_id': run['id'], 'workflow': run['workflow_name'], 'file_id': run['file_id'], 'message': job['payload']['message']}, job['id'])
    connection.execute("UPDATE automation_job SET status='done' WHERE id=%s", (job['id'],))
    workflow_event(connection, run['workflow_id'], run['id'], 'delivery_accepted', 'Automation', job['kind'], run['current_node'])
    remaining = connection.execute("SELECT id FROM automation_job WHERE run_id=%s AND payload->>'node'=%s AND id<>%s AND status<>'done' LIMIT 1", (run['id'], run['current_node'], job['id'])).fetchone()
    if not remaining:
        graph = Graph.model_validate(run['graph'])
        target = next(edge.target for edge in graph.edges if edge.source == run['current_node'] and edge.sourceHandle == 'next')
        advance(connection, run, target)
    return 'done', ''


def process_one(connection):
    job = connection.execute("SELECT * FROM automation_job WHERE status IN ('queued','retry') AND next_at<=clock_timestamp() ORDER BY next_at,id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
    if not job:
        return False
    attempts = job['attempts'] + 1
    try:
        with connection.transaction():
            result, detail = execute_job(connection, job)
    except Exception as error:
        result = 'failed' if attempts >= 5 else 'retry'
        detail = error.detail if isinstance(error, HTTPException) and isinstance(error.detail, str) else 'Automation execution failed. Check service configuration and retry.'
        logger.warning('Automation job %s attempt %s failed (%s)', job['id'], attempts, type(error).__name__)
    connection.execute("UPDATE automation_job SET status=%s,attempts=%s,last_error=%s,next_at=clock_timestamp()+(%s * INTERVAL '1 second'),updated_at=clock_timestamp() WHERE id=%s", (result, attempts, detail, min(3600, 30 * 2 ** min(attempts, 7)), job['id']))
    if result == 'failed':
        connection.execute("""INSERT INTO notification(company_id,team_id,admin_id,kind,message,run_id,event_key)
            SELECT job.company_id,file.team_id,admin.id,'automation_failure',
                flow.name || ': automation failed after retries - ' || file.name,job.run_id,%s
            FROM automation_job job JOIN workflow flow ON flow.id=job.workflow_id
            JOIN stored_file file ON file.id=job.file_id AND file.company_id=job.company_id
            JOIN admin ON admin.company_id=job.company_id AND admin.role='admin' AND admin.is_active
            WHERE job.id=%s ON CONFLICT DO NOTHING""", (f"automation-failure:{job['id']}", job['id']))
        connection.execute("""INSERT INTO notification(company_id,team_id,account_id,kind,message,run_id,event_key)
            SELECT job.company_id,file.team_id,owner.id,'automation_failure',
                flow.name || ': automation failed after retries - ' || file.name,job.run_id,%s
            FROM automation_job job JOIN workflow flow ON flow.id=job.workflow_id
            JOIN stored_file file ON file.id=job.file_id AND file.company_id=job.company_id
            JOIN team_account owner ON owner.id=file.owner_id AND owner.company_id=file.company_id
                AND owner.team_id=file.team_id AND owner.status='active'
            WHERE job.id=%s ON CONFLICT DO NOTHING""", (f"automation-failure:{job['id']}", job['id']))
    return True


def tick():
    with connect() as connection:
        enqueue_schedules(connection)
    for _ in range(10):
        with connect() as connection:
            if not process_one(connection):
                break


def create_automation_router(company_dependency, origin_dependency):
    router = APIRouter()

    @router.get('/company/teams/workflows/jobs')
    def jobs(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), page: int = Query(1, ge=1, le=100000), search: str = Query('', max_length=100), from_date: date | None = None, to_date: date | None = None):
        dates, values = date_clause(from_date, to_date, 'job.created_at')
        return list_rows(connection, 'automation_job job JOIN workflow flow ON flow.id=job.workflow_id', 'job.id,job.run_id,job.kind,job.status,job.attempts,job.last_error,job.created_at,job.next_at,flow.name AS workflow_name', f"job.company_id=%s AND strpos(lower(flow.name || ' ' || job.kind || ' ' || job.status),lower(%s))>0 AND {dates}", [admin['company_id'], search, *values], page, 'job.created_at DESC,job.id DESC')

    @router.post('/company/teams/workflows/jobs/{identifier}/retry', dependencies=[Depends(origin_dependency)])
    def retry(identifier: UUID, payload: Note, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        changed = connection.execute("UPDATE automation_job SET status='queued',attempts=0,last_error='',next_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s AND company_id=%s AND status IN ('failed','retry') RETURNING id", (identifier, admin['company_id'])).fetchone()
        if not changed:
            raise HTTPException(409, 'Job is missing or not retryable.')
        return {'detail': 'Automation queued for retry.'}

    return router