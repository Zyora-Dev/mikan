import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from zoho_authorize import CREDENTIALS_PATH, AuthorizationError, load_credentials
from zoho_inventory import REPORT_DIRECTORY, ROOT_IDS, InventoryError, WorkDriveReader, children


GENERAL_ID = ROOT_IDS[0]
COMPANY_NAME = 'Mikan Engineering Pvt Ltd'


def load_plan(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        root = connection.execute('SELECT * FROM roots WHERE id=?', (GENERAL_ID,)).fetchone()
        if not root or root['name'] != 'General' or root['partial_access']:
            raise InventoryError('A complete, fully accessible General inventory is required.')
        entries = [dict(row) for row in connection.execute('SELECT * FROM entries WHERE root_id=?', (GENERAL_ID,))]
    by_id = {entry['id']: entry for entry in entries}
    paths = {}

    def resolve(entry, ancestors):
        identifier = entry['id']
        if identifier in paths:
            return paths[identifier]
        if identifier in ancestors or not re.fullmatch(r'[A-Za-z0-9]+', identifier):
            raise InventoryError('Invalid or cyclic source identifier.')
        name = entry['name']
        if not name or name != name.strip() or name in ('.', '..') or '/' in name or '\\' in name or any(ord(character) < 32 or ord(character) == 127 for character in name):
            raise InventoryError('A source name cannot be preserved safely; nothing renamed.')
        if identifier == GENERAL_ID:
            if name != 'General' or entry['parent_id'] is not None or not entry['is_folder']:
                raise InventoryError('Invalid General wrapper.')
            parent = ''
        else:
            parent_entry = by_id.get(entry['parent_id'])
            if not parent_entry or not parent_entry['is_folder']:
                raise InventoryError('Missing source parent folder.')
            parent = resolve(parent_entry, ancestors | {identifier})
        path_value = f'{parent}/{name}' if parent else name
        if len(path_value) > 255:
            raise InventoryError('A source path exceeds the destination limit; nothing truncated.')
        if entry['is_folder'] and not entry['scanned']:
            raise InventoryError('General inventory has unscanned folders.')
        if not entry['is_folder'] and (entry['native_kind'] or type(entry['size_bytes']) is not int or entry['size_bytes'] < 0):
            raise InventoryError('Unsupported native document or unknown file size.')
        entry['parent'] = parent
        entry['path'] = path_value
        paths[identifier] = path_value
        return path_value

    if GENERAL_ID not in by_id:
        raise InventoryError('General wrapper missing from inventory.')
    for entry in entries:
        resolve(entry, set())
    if len(set(paths.values())) != len(entries):
        raise InventoryError('Duplicate destination paths; nothing overwritten.')
    return sorted(entries, key=lambda entry: (not entry['is_folder'], entry['path'].count('/'), entry['path']))


def plan_summary(plan):
    return {
        'company': COMPANY_NAME, 'destination': 'Company Root / General',
        'folders_including_general': sum(bool(entry['is_folder']) for entry in plan),
        'files': sum(not entry['is_folder'] for entry in plan),
        'bytes': sum(entry['size_bytes'] for entry in plan if not entry['is_folder']),
    }


def check_source_record(record, entry):
    attributes = record.get('attributes', {})
    if (record.get('id') != entry['id'] or attributes.get('name') != entry['name']
            or attributes.get('parent_id') != entry['parent_id']
            or attributes.get('is_folder') is not bool(entry['is_folder'])):
        raise InventoryError('Source identity or hierarchy changed; refresh the inventory before proceeding.')
    if not entry['is_folder']:
        size = (attributes.get('storage_info') or {}).get('size_in_bytes')
        if str(size) != str(entry['size_bytes']) or str(attributes.get('modified_time_in_millisecond', '')) != entry['modified_at']:
            raise InventoryError('Source file changed since inventory; nothing overwritten.')
    return attributes


def verify_source(reader, plan):
    root = reader.get(f'/teamfolders/{GENERAL_ID}').get('data', {})
    if root.get('id') != GENERAL_ID or root.get('attributes', {}).get('name') != 'General' or root.get('attributes', {}).get('is_partial_lib'):
        raise InventoryError('General source identity or access changed.')
    for folder in (entry for entry in plan if entry['is_folder']):
        namespace = 'teamfolders' if folder['id'] == GENERAL_ID else 'files'
        records = list(children(reader, f"/{namespace}/{folder['id']}/files"))
        expected = {entry['id']: entry for entry in plan if entry['parent_id'] == folder['id']}
        if {record['id'] for record in records} != set(expected):
            raise InventoryError('General folder contents changed since inventory; migration stopped.')
        for record in records:
            check_source_record(record, expected[record['id']])


def download_source(reader, entry, output):
    attributes = check_source_record(reader.get(f"/files/{entry['id']}").get('data', {}), entry)
    raw_url = attributes.get('download_url')
    if not isinstance(raw_url, str):
        raise InventoryError('Source did not provide a download URL.')
    url = httpx.URL(raw_url)
    if (url.scheme != 'https' or url.host != 'download-accl.zoho.in' or url.port not in (None, 443)
            or url.userinfo or url.fragment or url.path != f"/v1/workdrive/download/{entry['id']}"):
        raise InventoryError('Unverified download host or path; credentials were not sent.')
    digest = hashlib.sha256()
    length = 0
    with reader.client.stream('GET', url, follow_redirects=False,
            headers={'Authorization': f'Zoho-oauthtoken {reader.access_token}', 'Accept-Encoding': 'identity'}) as response:
        if response.status_code != 200:
            raise InventoryError(f'Zoho download HTTP {response.status_code}; no retry or redirect followed.')
        for chunk in response.iter_bytes(1024 * 1024):
            length += len(chunk)
            if length > entry['size_bytes']:
                raise InventoryError('Source download exceeds inventoried size.')
            output.write(chunk)
            digest.update(chunk)
    if length != entry['size_bytes']:
        raise InventoryError('Source download size mismatch.')
    check_source_record(reader.get(f"/files/{entry['id']}").get('data', {}), entry)
    output.seek(0)
    return digest.hexdigest()


def destination_context(connection, plan):
    from insights import COMPANY_STORAGE_CAPACITY_BYTES

    companies = connection.execute('SELECT id,name FROM company WHERE name=%s', (COMPANY_NAME,)).fetchall()
    if len(companies) != 1:
        raise InventoryError('Expected exactly one destination company named Mikan Engineering Pvt Ltd.')
    company = companies[0]
    storage = connection.execute('''SELECT storage.*,policy.max_file_bytes FROM company_upload_policy policy
        JOIN storage_connection storage ON storage.id=policy.connection_id
        WHERE policy.company_id=%s AND policy.enabled AND storage.enabled''', (company['id'],)).fetchone()
    if not storage:
        raise InventoryError('The destination company needs an enabled upload policy and storage connection.')
    if any(entry['size_bytes'] > min(storage['max_file_bytes'], 5_000_000_000) for entry in plan if not entry['is_folder']):
        raise InventoryError('A General file exceeds the configured upload limit or single-object limit.')
    legacy = connection.execute('''SELECT 1 FROM stored_file WHERE company_id=%s AND state NOT IN ('purged','cancelled')
        AND (folder='General' OR left(folder,8)='General/' OR (folder='' AND name='General'))
        UNION ALL SELECT 1 FROM data_folder WHERE company_id=%s AND (path='General' OR left(path,8)='General/') LIMIT 1''',
        (company['id'], company['id'])).fetchone()
    if legacy:
        raise InventoryError('An existing team drive uses General; refusing to merge or overwrite it.')
    existing = {row['source_id']: row for row in connection.execute(
        "SELECT * FROM company_root_entry WHERE company_id=%s AND (path='General' OR left(path,8)='General/' OR source_id=ANY(%s))",
        (company['id'], [entry['id'] for entry in plan])).fetchall()}
    expected = {entry['id']: entry for entry in plan}
    if any(identifier not in expected for identifier in existing):
        raise InventoryError('Destination General contains unrelated records; nothing overwritten.')
    for identifier, record in existing.items():
        entry = expected[identifier]
        if (record['path'] != entry['path'] or record['kind'] != ('folder' if entry['is_folder'] else 'file')
                or (not entry['is_folder'] and (record['size_bytes'] != entry['size_bytes']
                    or record['source_modified_at'] != entry['modified_at'] or record['connection_id'] != storage['id']))):
            raise InventoryError('An existing migration record does not match this source snapshot or storage connection.')
    allocated = connection.execute('SELECT coalesce(sum(greatest(storage_quota_bytes,storage_used_bytes)),0) AS bytes FROM team WHERE company_id=%s', (company['id'],)).fetchone()['bytes']
    root_bytes = connection.execute('SELECT coalesce(sum(size_bytes),0) AS bytes FROM company_root_entry WHERE company_id=%s', (company['id'],)).fetchone()['bytes']
    missing_bytes = sum(entry['size_bytes'] for entry in plan if not entry['is_folder'] and entry['id'] not in existing)
    if allocated + root_bytes + missing_bytes > COMPANY_STORAGE_CAPACITY_BYTES:
        raise InventoryError('Insufficient unallocated company storage; team allocations will not be changed.')
    return company, storage, existing


def reserve_destination(connection, plan):
    from company_root import RootFolderInput, create_root_folder, lock_root

    with connection.transaction():
        company, _, _ = destination_context(connection, plan)
        lock_root(connection, company['id'])
        connection.execute('LOCK TABLE stored_file,data_folder IN SHARE ROW EXCLUSIVE MODE')
        connection.execute('SELECT id FROM team WHERE company_id=%s ORDER BY id FOR UPDATE', (company['id'],)).fetchall()
        company, storage, existing = destination_context(connection, plan)
        for entry in plan:
            if entry['id'] in existing:
                continue
            if entry['is_folder']:
                record = create_root_folder(connection, company['id'], RootFolderInput(name=entry['name'], parent=entry['parent']), entry['id'])
            else:
                identifier = uuid4()
                record = connection.execute('''INSERT INTO company_root_entry
                    (id,company_id,kind,parent,name,source_id,source_modified_at,size_bytes,connection_id,object_key,state)
                    VALUES (%s,%s,'file',%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING *''',
                    (identifier, company['id'], entry['parent'], entry['name'], entry['id'], entry['modified_at'],
                     entry['size_bytes'], storage['id'], f"mikan/company-root/{company['id']}/{identifier}")).fetchone()
            existing[entry['id']] = record
        connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
            (company['id'], 'Zoho migration', 'root_import_reserve', 'General', json.dumps(plan_summary(plan))))
    return company, storage, existing


def object_head(client, storage, record):
    options = {'Bucket': storage['bucket'], 'Key': record['object_key']}
    if record.get('object_version'):
        options['VersionId'] = record['object_version']
    try:
        return client.head_object(**options)
    except ClientError as error:
        if str(error.response.get('Error', {}).get('Code')) in ('404', 'NoSuchKey', 'NotFound'):
            return None
        raise


def verify_object(client, storage, record, digest):
    head = object_head(client, storage, record)
    if not head or head['ContentLength'] != record['size_bytes']:
        raise InventoryError('Destination object missing or size mismatch; file not marked ready.')
    options = {'Bucket': storage['bucket'], 'Key': record['object_key'], 'IfMatch': head['ETag']}
    if head.get('VersionId'):
        options['VersionId'] = head['VersionId']
    response = client.get_object(**options)
    actual = hashlib.sha256()
    length = 0
    body = response['Body']
    try:
        while chunk := body.read(1024 * 1024):
            actual.update(chunk)
            length += len(chunk)
            if length > record['size_bytes']:
                raise InventoryError('Destination readback exceeds expected size.')
    finally:
        body.close()
    if length != record['size_bytes'] or actual.hexdigest() != digest:
        raise InventoryError('Destination SHA256 mismatch; object retained for investigation, file not published.')
    return head


def transfer_file(connection, reader, client, storage, record, entry):
    if record['state'] == 'ready':
        verify_object(client, storage, record, record['sha256'])
        return
    if shutil.disk_usage(tempfile.gettempdir()).free < entry['size_bytes'] + 128 * 1024 * 1024:
        raise InventoryError('Insufficient temporary disk space for the next file.')
    with tempfile.TemporaryFile() as output:
        digest = download_source(reader, entry, output)
        with connection.transaction():
            bound = connection.execute('''UPDATE company_root_entry SET sha256=%s WHERE id=%s AND company_id=%s
                AND state='pending' AND (sha256 IS NULL OR sha256=%s) RETURNING id''',
                (digest, record['id'], record['company_id'], digest)).fetchone()
            if not bound:
                raise InventoryError('Pending file content identity or state changed; nothing overwritten.')
        if object_head(client, storage, record) is None:
            client.put_object(Bucket=storage['bucket'], Key=record['object_key'], Body=output,
                ContentLength=entry['size_bytes'], ContentType='application/octet-stream', IfNoneMatch='*',
                Metadata={'sha256': digest, 'zoho-source-id': entry['id']})
        head = verify_object(client, storage, record, digest)
    with connection.transaction():
        published = connection.execute('''UPDATE company_root_entry SET state='ready',etag=%s,object_version=%s,
            uploaded_at=clock_timestamp() WHERE id=%s AND company_id=%s AND state='pending' AND sha256=%s RETURNING id''',
            (head['ETag'], head.get('VersionId'), record['id'], record['company_id'], digest)).fetchone()
        if not published:
            raise InventoryError('Destination state changed before publication; rerun to reconcile.')
        connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
            (record['company_id'], 'Zoho migration', 'root_file_import', entry['path'], f"{entry['size_bytes']} bytes; SHA256 {digest}"))


def migrate(connection, reader, plan):
    from uploads import storage_client

    company, storage, _ = destination_context(connection, plan)
    lock_key = f"zoho-general:{company['id']}"
    if not connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired', (lock_key,)).fetchone()['acquired']:
        raise InventoryError('Another General migration is running; nothing started.')
    try:
        verify_source(reader, plan)
        client = storage_client(storage)
        try:
            client.head_bucket(Bucket=storage['bucket'])
            company, storage, existing = reserve_destination(connection, plan)
            files = [entry for entry in plan if not entry['is_folder']]
            verified_bytes = 0
            for number, entry in enumerate(files, 1):
                print(f"Verifying/transferring {number}/{len(files)}: {entry['path']} ({entry['size_bytes']} bytes)", flush=True)
                transfer_file(connection, reader, client, storage, existing[entry['id']], entry)
                verified_bytes += entry['size_bytes']
                print(f'Verified checkpoint: {number} files, {verified_bytes} bytes.', flush=True)
            verify_source(reader, plan)
            _, _, completed = destination_context(connection, plan)
            if len(completed) != len(plan) or any(record['state'] != 'ready' for record in completed.values()):
                raise InventoryError('Final destination reconciliation failed.')
            print('Migration complete: exact hierarchy and all file SHA256 readbacks verified. Zoho originals unchanged.', flush=True)
        finally:
            client.close()
    finally:
        connection.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (lock_key,))


def migration_dsn():
    dsn = os.environ.get('MIKAN_MIGRATION_DATABASE_URL')
    if not dsn:
        raise InventoryError('Set MIKAN_MIGRATION_DATABASE_URL privately to the production database URL. There is no local/default database fallback.')
    options = conninfo_to_dict(dsn)
    host = options.get('host', '')
    if not host or host.startswith(('/', '.', '127.')) or host.lower() in ('localhost', '::1') or ',' in host or 'hostaddr' in options or 'service' in options:
        raise InventoryError('A single explicit non-local production database host is required.')
    return dsn


def main(argv=None):
    parser = argparse.ArgumentParser(description='Copy only General into Mikan Company Root, preserving originals and verifying every byte.')
    parser.add_argument('--inventory', type=Path, default=REPORT_DIRECTORY / 'inventory.sqlite3')
    parser.add_argument('--verify-source', action='store_true', help='Reconcile General live and download its smallest file without destination writes.')
    parser.add_argument('--check-destination', action='store_true', help='Read-only production company, storage, quota and collision checks.')
    parser.add_argument('--execute', action='store_true', help='Reserve and copy General; reruns resume verified records without overwriting objects.')
    parser.add_argument('--confirm-company', help='Required for execution; must exactly match the destination company name.')
    parser.add_argument('--backup-confirmed', action='store_true', help='Confirm a current production database backup exists before execution.')
    parser.add_argument('--credentials', type=Path, default=CREDENTIALS_PATH, help='Private Zoho credential file; never pass secrets as arguments.')
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.inventory)
        print(json.dumps(plan_summary(plan), indent=2), flush=True)
        if args.execute and (args.confirm_company != COMPANY_NAME or not args.backup_confirmed):
            raise InventoryError('Execution requires --confirm-company "Mikan Engineering Pvt Ltd" and --backup-confirmed.')
        dsn = migration_dsn() if args.execute or args.check_destination else None
        if args.check_destination or args.execute:
            with psycopg.connect(dsn, sslmode='require', connect_timeout=10, autocommit=True, row_factory=dict_row) as connection:
                with connection.transaction():
                    connection.execute('SET TRANSACTION READ ONLY')
                    company, storage, existing = destination_context(connection, plan)
                print(f"Destination checked: {company['name']}; storage connection {storage['id']}; {len(existing)} resumable records.", flush=True)
                if args.execute:
                    credentials, _ = load_credentials(args.credentials, require_refresh=True)
                    with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
                        migrate(connection, WorkDriveReader(client, credentials), plan)
                    return 0
        if args.verify_source:
            credentials, _ = load_credentials(args.credentials, require_refresh=True)
            with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
                reader = WorkDriveReader(client, credentials)
                verify_source(reader, plan)
                smallest = min((entry for entry in plan if not entry['is_folder']), key=lambda entry: entry['size_bytes'])
                with tempfile.TemporaryFile() as output:
                    digest = download_source(reader, smallest, output)
                print(f"Source verified; download probe {smallest['size_bytes']} bytes, SHA256 {digest}.", flush=True)
        print('Read-only plan. No files transferred or destination records created.', flush=True)
        return 0
    except (InventoryError, AuthorizationError) as error:
        print(f'Migration stopped: {error}', flush=True)
        return 1
    except (sqlite3.Error, psycopg.Error, OSError, httpx.HTTPError, ValueError, BotoCoreError, ClientError, HTTPException) as error:
        print(f'Migration stopped ({type(error).__name__}); provider details hidden, no automatic retry.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())