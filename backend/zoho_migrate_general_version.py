import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from psycopg.rows import dict_row

from zoho_authorize import CREDENTIALS_PATH, AuthorizationError, load_credentials
from zoho_inventory import InventoryError, WorkDriveReader, response_json
from zoho_migrate import COMPANY_NAME, migration_dsn, object_head, verify_object


SOURCE_FILE_ID = 'qlqohc29216c5698145ac81c30eab424ca9a4'
SOURCE_VERSION_ID = f'{SOURCE_FILE_ID}-4017837000024806802'
FILE_NAME = 'VALVE PIT GA DRAWING.pdf'
VERSION_LABEL = '1.0'
VERSION_SIZE = 515_641
VERSION_SHA256 = '9baadfd2c992a70055a83f1af05b8884ef94855b5cec1ad0362418b5c2f0f51d'


def historical_url(reader):
    metadata = reader.version_preview_info(SOURCE_VERSION_ID)
    attributes = metadata.get('data', {}).get('attributes', {})
    raw_url = attributes.get('preview_data_url')
    if not isinstance(raw_url, str):
        raise InventoryError('Source did not provide the historical download URL.')
    url = httpx.URL(raw_url)
    if (url.scheme != 'https' or url.host != 'download-accl.zoho.in' or url.port not in (None, 443)
            or url.userinfo or url.fragment or url.path != f'/v1/workdrive/previewdata/{SOURCE_FILE_ID}'
            or not url.params.get('version')):
        raise InventoryError('Unverified historical download URL; credentials were not sent.')
    reported_size = attributes.get('file_size') or attributes.get('size')
    if isinstance(reported_size, int) or (isinstance(reported_size, str) and reported_size.isdigit()):
        if int(reported_size) != VERSION_SIZE:
            raise InventoryError('Historical source size changed; migration stopped.')
    return url


def download_version(reader, output):
    url = historical_url(reader)
    digest = hashlib.sha256()
    length = 0
    prefix = b''
    with reader.client.stream('GET', url, follow_redirects=False,
            headers={'Authorization': f'Zoho-oauthtoken {reader.access_token}', 'Accept-Encoding': 'identity'}) as response:
        if response.status_code != 200 or 'location' in response.headers:
            raise InventoryError(f'Zoho historical download HTTP {response.status_code}; no retry or redirect followed.')
        for chunk in response.iter_bytes(1024 * 1024):
            if not prefix:
                prefix = chunk[:8]
            length += len(chunk)
            if length > VERSION_SIZE:
                raise InventoryError('Historical source exceeds the verified size.')
            output.write(chunk)
            digest.update(chunk)
    if length != VERSION_SIZE or digest.hexdigest() != VERSION_SHA256 or not prefix.startswith(b'%PDF-'):
        raise InventoryError('Historical source byte verification failed; nothing published.')
    output.seek(0)
    return digest.hexdigest()


def destination_context(connection):
    from insights import COMPANY_STORAGE_CAPACITY_BYTES

    rows = connection.execute('SELECT id,name FROM company WHERE name=%s', (COMPANY_NAME,)).fetchall()
    if len(rows) != 1:
        raise InventoryError('Expected exactly one destination company named Mikan Engineering Pvt Ltd.')
    company = rows[0]
    file = connection.execute("""SELECT * FROM company_root_entry WHERE company_id=%s AND source_id=%s
        AND kind='file' AND state='ready'""", (company['id'], SOURCE_FILE_ID)).fetchone()
    if not file or file['name'] != FILE_NAME or file['size_bytes'] != 1_494_625:
        raise InventoryError('The verified current General PDF was not found; nothing changed.')
    storage = connection.execute("""SELECT storage.*,policy.max_file_bytes FROM company_upload_policy policy
        JOIN storage_connection storage ON storage.id=policy.connection_id
        WHERE policy.company_id=%s AND policy.enabled AND storage.enabled AND storage.id=%s""",
        (company['id'], file['connection_id'])).fetchone()
    if not storage or VERSION_SIZE > storage['max_file_bytes']:
        raise InventoryError('The current file storage connection cannot accept this version.')
    version = connection.execute('SELECT * FROM company_root_version WHERE file_id=%s AND source_version_id=%s',
        (file['id'], SOURCE_VERSION_ID)).fetchone()
    if version and (version['company_id'] != company['id'] or version['name'] != FILE_NAME
            or version['version_label'] != VERSION_LABEL or version['size_bytes'] != VERSION_SIZE
            or version['connection_id'] != storage['id'] or version['sha256'] not in (None, VERSION_SHA256)):
        raise InventoryError('Existing historical migration record does not match the verified source.')
    allocated = connection.execute('SELECT coalesce(sum(greatest(storage_quota_bytes,storage_used_bytes)),0) AS bytes FROM team WHERE company_id=%s',
        (company['id'],)).fetchone()['bytes']
    root_bytes = connection.execute('SELECT coalesce(sum(size_bytes),0) AS bytes FROM company_root_entry WHERE company_id=%s',
        (company['id'],)).fetchone()['bytes']
    history_bytes = connection.execute("SELECT coalesce(sum(size_bytes),0) AS bytes FROM company_root_version WHERE company_id=%s AND state='ready'",
        (company['id'],)).fetchone()['bytes']
    missing = 0 if version and version['state'] == 'ready' else VERSION_SIZE
    if allocated + root_bytes + history_bytes + missing > COMPANY_STORAGE_CAPACITY_BYTES:
        raise InventoryError('Insufficient unallocated company storage; team allocations were not changed.')
    return company, storage, file, version


def reserve_version(connection):
    with connection.transaction():
        company, storage, file, version = destination_context(connection)
        connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'company-root:{company["id"]}',))
        company, storage, file, version = destination_context(connection)
        if not version:
            identifier = uuid4()
            version = connection.execute("""INSERT INTO company_root_version
                (id,file_id,company_id,source_version_id,version_label,connection_id,object_key,name,size_bytes,sha256)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (identifier, file['id'], company['id'], SOURCE_VERSION_ID, VERSION_LABEL, storage['id'],
                 f'mikan/company-root/{company["id"]}/versions/{identifier}', FILE_NAME, VERSION_SIZE, VERSION_SHA256)).fetchone()
        return company, storage, file, version


def migrate(connection, reader):
    from uploads import storage_client

    company, _, _, _ = destination_context(connection)
    lock_key = f'zoho-general-version:{company["id"]}:{SOURCE_VERSION_ID}'
    if not connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired', (lock_key,)).fetchone()['acquired']:
        raise InventoryError('This historical version migration is already running.')
    try:
        company, storage, file, version = reserve_version(connection)
        client = storage_client(storage)
        try:
            client.head_bucket(Bucket=storage['bucket'])
            if version['state'] == 'ready':
                verify_object(client, storage, version, VERSION_SHA256)
                print('Already complete: historical version destination readback verified.', flush=True)
                return
            if shutil.disk_usage(tempfile.gettempdir()).free < VERSION_SIZE + 128 * 1024 * 1024:
                raise InventoryError('Insufficient temporary disk space.')
            with tempfile.TemporaryFile() as output:
                digest = download_version(reader, output)
                if object_head(client, storage, version) is None:
                    client.put_object(Bucket=storage['bucket'], Key=version['object_key'], Body=output,
                        ContentLength=VERSION_SIZE, ContentType='application/pdf', IfNoneMatch='*',
                        Metadata={'sha256': digest, 'zoho-source-id': SOURCE_FILE_ID,
                                  'zoho-source-version-id': SOURCE_VERSION_ID})
                head = verify_object(client, storage, version, digest)
            with connection.transaction():
                published = connection.execute("""UPDATE company_root_version SET state='ready',etag=%s,object_version=%s,
                    uploaded_at=clock_timestamp() WHERE id=%s AND company_id=%s AND state='pending' AND sha256=%s RETURNING id""",
                    (head['ETag'], head.get('VersionId'), version['id'], company['id'], digest)).fetchone()
                if not published:
                    raise InventoryError('Destination state changed before publication; rerun to reconcile.')
                connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
                    (company['id'], 'Zoho migration', 'root_version_import', file['path'],
                     f'Version {VERSION_LABEL}; {VERSION_SIZE} bytes; SHA256 {digest}'))
            ready = connection.execute('SELECT * FROM company_root_version WHERE id=%s AND company_id=%s AND state=\'ready\'',
                (version['id'], company['id'])).fetchone()
            verify_object(client, storage, ready, VERSION_SHA256)
            print('Migration complete: General PDF version 1.0 stored and SHA256 readback verified. Current version unchanged; Zoho original unchanged.', flush=True)
        finally:
            client.close()
    finally:
        connection.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (lock_key,))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Copy the one verified missing General PDF version into Company Root history.')
    parser.add_argument('--check-destination', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-company')
    parser.add_argument('--backup-confirmed', action='store_true')
    parser.add_argument('--credentials', type=Path, default=CREDENTIALS_PATH)
    args = parser.parse_args(argv)
    try:
        if args.execute and (args.confirm_company != COMPANY_NAME or not args.backup_confirmed):
            raise InventoryError('Execution requires --confirm-company "Mikan Engineering Pvt Ltd" and --backup-confirmed.')
        credentials = None
        if args.execute:
            try:
                credentials, _ = load_credentials(args.credentials, require_refresh=True)
            except FileNotFoundError:
                raise AuthorizationError(
                    f'Zoho credentials file is missing: {args.credentials}. Render redeploys remove this temporary file; recreate it with mode 600 before executing.'
                ) from None
        dsn = migration_dsn()
        with psycopg.connect(dsn, sslmode='require', connect_timeout=10, autocommit=True, row_factory=dict_row) as connection:
            with connection.transaction():
                connection.execute('SET TRANSACTION READ ONLY')
                company, storage, file, version = destination_context(connection)
            print(json.dumps({'company': company['name'], 'file': file['path'], 'current_bytes': file['size_bytes'],
                'missing_version': VERSION_LABEL, 'missing_bytes': VERSION_SIZE, 'storage_connection': storage['id'],
                'existing_state': version['state'] if version else None}, indent=2), flush=True)
            if not args.execute:
                print('Read-only destination check. No source downloaded and no destination changed.', flush=True)
                return 0
            with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
                migrate(connection, WorkDriveReader(client, credentials))
        return 0
    except (InventoryError, AuthorizationError) as error:
        print(f'Migration stopped: {error}', flush=True)
        return 1
    except (psycopg.Error, OSError, httpx.HTTPError, ValueError, BotoCoreError, ClientError) as error:
        print(f'Migration stopped ({type(error).__name__}); provider details hidden, no automatic retry.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())