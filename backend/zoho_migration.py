import hashlib
import json
import logging
import re
import shutil
import tempfile
import time
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from company_root import ROOT_ENTRIES, RootFolderInput, create_root_folder
from database import connect, get_db
from uploads import storage_client
from zoho_inventory import InventoryError, WorkDriveReader, children
from zoho_migrate import check_source_record, download_source, object_head, verify_object
from zoho_migrate_general_version import migration_credentials


MIKAN_ROOT_ID = '4ligq5e352ffc509e405c863fc17a7c4d83c1'
DESTINATION_ROOT = 'Mikan'
JOB_COLUMNS = '''id,source_folder_id,source_folder_name,destination_path,status,phase,
    folders_total,folders_complete,files_total,files_complete,versions_total,versions_complete,
    bytes_total,bytes_complete,attempts,last_error,created_at,started_at,completed_at,updated_at'''
LOGGER = logging.getLogger(__name__)


class MigrationDeferred(Exception):
    pass


class MigrationStart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_folder_id: str = Field(pattern=r'^[A-Za-z0-9]+$', min_length=1, max_length=200)


def exact_nonnegative_integer(*values):
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            continue
        if number.is_finite() and number >= 0 and number == number.to_integral_value():
            return int(number)
    return None


def source_size(attributes):
    storage = attributes.get('storage_info') or {}
    return exact_nonnegative_integer(
        storage.get('size_in_bytes'), attributes.get('size_in_bytes'),
        attributes.get('file_size'), attributes.get('size'),
    ) or 0


def exact_source_size(attributes):
    storage = attributes.get('storage_info') or {}
    value = exact_nonnegative_integer(
        storage.get('size_in_bytes'), attributes.get('size_in_bytes'),
        attributes.get('file_size'), attributes.get('size'),
    )
    if value is not None:
        return value
    raise InventoryError('A source file has no exact byte size.')


def source_folder(record):
    attributes = record.get('attributes')
    identifier = record.get('id')
    if not isinstance(attributes, dict) or not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9]+', identifier):
        raise InventoryError('Unexpected source folder metadata.')
    name = attributes.get('name')
    if attributes.get('is_folder') is not True or not isinstance(name, str) or not name or name != name.strip():
        raise InventoryError('Unexpected source folder metadata.')
    if name in ('.', '..') or '/' in name or '\\' in name or len(name) > 255:
        raise InventoryError('A source folder name cannot be preserved safely.')
    return {'source_folder_id': identifier, 'name': name, 'size_bytes': source_size(attributes)}


def clean_name(value):
    if (not isinstance(value, str) or not value or value != value.strip() or value in ('.', '..')
            or '/' in value or '\\' in value or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise InventoryError('A source name cannot be preserved safely.')
    return value


def reader_relationship(reader, file_id, relationship):
    if relationship not in ('versions', 'approvedversions') or not re.fullmatch(r'[A-Za-z0-9]+', file_id):
        raise InventoryError('Invalid version metadata request.')
    if not reader.access_token or time.monotonic() >= reader.expires_at:
        reader.refresh()
    for attempt in range(2):
        response = reader.client.get(
            f'{reader.api_domain}/workdrive/api/v1/files/{file_id}/{relationship}',
            headers={'Authorization': f'Zoho-oauthtoken {reader.access_token}'},
        )
        reader.requests += 1
        if response.status_code != 401 or attempt:
            break
        reader.refresh()
    context = f'Zoho {relationship} metadata for file {file_id}'
    if response.status_code != 200:
        raise InventoryError(f'{context} returned HTTP {response.status_code}; migration stopped.')
    try:
        payload = response.json()
    except ValueError:
        raise InventoryError(f'{context} returned invalid JSON; response content hidden.') from None
    if not isinstance(payload, dict):
        raise InventoryError(f'{context} returned an unexpected response shape; content hidden.')
    if payload.get('error') or payload.get('errors'):
        raise InventoryError(f'{context} returned a structured API error; content hidden.')
    records = payload.get('data')
    if not isinstance(records, list):
        raise InventoryError(f'{context} returned an unexpected data shape; content hidden.')
    return records


def version_item(record, file_item):
    attributes = record.get('attributes')
    identifier = record.get('id')
    if not isinstance(attributes, dict) or not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]+', identifier):
        raise InventoryError('Unexpected source version metadata.')
    storage = attributes.get('storage_info') or {}
    size = exact_nonnegative_integer(
        attributes.get('file_size'), attributes.get('size_in_bytes'),
        storage.get('size_in_bytes'), attributes.get('size'),
    )
    if size is None:
        raise InventoryError('A source version has no exact byte size.')
    label = attributes.get('version_number')
    if not isinstance(label, (str, int, float)):
        raise InventoryError('A source version has no version label.')
    return {
        'kind': 'version', 'source_id': identifier, 'source_parent_id': file_item['source_parent_id'],
        'source_file_id': file_item['source_id'], 'source_version_id': identifier,
        'source_name': file_item['source_name'], 'destination_path': file_item['destination_path'],
        'version_label': str(label), 'size_bytes': size,
        'source_created_at': str(attributes.get('created_time_in_millisecond') or attributes.get('created_time') or ''),
        'source_modified_at': str(attributes.get('modified_time_in_millisecond') or attributes.get('modified_time') or ''),
        'source_metadata': attributes,
    }


def historical_versions(active_records, approved_records, file_item):
    active = [version_item(record, file_item) for record in active_records]
    if not active:
        raise InventoryError('Source file has no active version metadata; current version cannot be reconciled.')
    versions = {item['source_id']: item for item in active}
    for record in approved_records:
        item = version_item(record, file_item)
        previous = versions.get(item['source_id'])
        if previous and (previous['size_bytes'], previous['version_label']) != (item['size_bytes'], item['version_label']):
            raise InventoryError('Conflicting source version metadata.')
        versions[item['source_id']] = item
    try:
        numbered = [(item, Decimal(item['version_label'])) for item in active]
        highest = max(number for _item, number in numbered)
    except (InvalidOperation, ValueError):
        raise InventoryError('Active source versions have non-numeric labels; current version cannot be reconciled.') from None
    current = [item for item, number in numbered if number == highest]
    if len(current) != 1 or current[0]['size_bytes'] != file_item['size_bytes']:
        raise InventoryError('Current source version does not reconcile with the current file.')
    versions.pop(current[0]['source_id'])
    return list(versions.values()), current[0]['source_id']


def inventory_folder(reader, source_id, root_name):
    items = []
    seen = set()

    def visit(metadata, parent_id, parent_path, expected_name=None):
        folder_id = metadata.get('id')
        attributes = metadata.get('attributes', {})
        name = clean_name(attributes.get('name'))
        if (not isinstance(folder_id, str) or not re.fullmatch(r'[A-Za-z0-9]+', folder_id)
                or attributes.get('is_folder') is not True or (expected_name and name != expected_name)):
            raise InventoryError('Source folder identity changed during inventory.')
        path = f'{parent_path}/{name}' if parent_path else name
        if len(path) > 255 or folder_id in seen:
            raise InventoryError('Source hierarchy is cyclic, repeated, or exceeds destination limits.')
        seen.add(folder_id)
        items.append({
            'kind': 'folder', 'source_id': folder_id, 'source_parent_id': parent_id, 'source_file_id': None,
            'source_version_id': None, 'source_name': name, 'destination_path': path, 'version_label': None,
            'size_bytes': 0, 'source_created_at': str(attributes.get('created_time_in_millisecond') or ''),
            'source_modified_at': str(attributes.get('modified_time_in_millisecond') or ''),
            'source_metadata': attributes,
        })
        for record in children(reader, f'/files/{folder_id}/files'):
            child_attributes = record.get('attributes', {})
            child_id = record.get('id')
            child_name = clean_name(child_attributes.get('name'))
            if child_attributes.get('is_folder') is True:
                visit(record, folder_id, path, child_name)
                continue
            if child_attributes.get('is_folder') is not False or child_attributes.get('is_zoho_file'):
                raise InventoryError('Unsupported native document or unknown source resource.')
            size = exact_source_size(child_attributes)
            file_path = f'{path}/{child_name}'
            if len(file_path) > 255 or child_id in seen:
                raise InventoryError('Source hierarchy is repeated or exceeds destination limits.')
            seen.add(child_id)
            file_item = {
                'kind': 'file', 'source_id': child_id, 'source_parent_id': folder_id, 'source_file_id': child_id,
                'source_version_id': None, 'source_name': child_name, 'destination_path': file_path,
                'version_label': None, 'size_bytes': size,
                'source_created_at': str(child_attributes.get('created_time_in_millisecond') or ''),
                'source_modified_at': str(child_attributes.get('modified_time_in_millisecond') or ''),
                'source_metadata': child_attributes,
            }
            items.append(file_item)
            historical, current_version_id = historical_versions(
                reader_relationship(reader, child_id, 'versions'),
                reader_relationship(reader, child_id, 'approvedversions'), file_item,
            )
            file_item['source_metadata']['current_version_id'] = current_version_id
            items.extend(historical)
    roots = [record for record in children(reader, f'/teamfolders/{MIKAN_ROOT_ID}/files', 'folders')
             if record.get('id') == source_id]
    if len(roots) != 1:
        raise InventoryError('Source folder is no longer uniquely present under Mikan.')
    visit(roots[0], MIKAN_ROOT_ID, DESTINATION_ROOT, root_name)
    if len({(item['kind'], item['source_id']) for item in items}) != len(items):
        raise InventoryError('Repeated source item detected during inventory.')
    return items


def list_source_folders():
    credentials = migration_credentials()
    timeout = httpx.Timeout(20, connect=5)
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        reader = WorkDriveReader(client, credentials)
        root = reader.get(f'/teamfolders/{MIKAN_ROOT_ID}').get('data', {})
        attributes = root.get('attributes', {})
        if root.get('id') != MIKAN_ROOT_ID or attributes.get('name') != 'Mikan' or attributes.get('is_partial_lib'):
            raise InventoryError('Mikan source identity or access changed.')
        return [source_folder(record) for record in children(reader, f'/teamfolders/{MIKAN_ROOT_ID}/files', 'folders')]


def job_dict(record):
    return {key: (str(value) if isinstance(value, UUID) else value) for key, value in dict(record).items()}


def select_job(connection, company_id, source_folder_id):
    return connection.execute(
        f'SELECT {JOB_COLUMNS} FROM zoho_migration_job WHERE company_id=%s AND source_root_id=%s AND source_folder_id=%s',
        (company_id, MIKAN_ROOT_ID, source_folder_id),
    ).fetchone()


def try_lock_root(connection, company_id):
    acquired = connection.execute(
        'SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired',
        (f'company-root:{company_id}',),
    ).fetchone()['acquired']
    if not acquired:
        raise MigrationDeferred('Company Root is busy; migration will retry automatically.')


def repair_legacy_destination(connection, job, items):
    old_root = job['source_folder_name']
    new_root = f'{DESTINATION_ROOT}/{old_root}'
    if job['destination_path'] == new_root:
        return items
    if job['destination_path'] != old_root or any(
            item['destination_path'] != old_root and not item['destination_path'].startswith(old_root + '/')
            for item in items):
        raise InventoryError('Migration destination checkpoint is inconsistent; nothing moved.')
    if any(len(f'{DESTINATION_ROOT}/{item["destination_path"]}') > 255 for item in items):
        raise InventoryError('Correct Mikan destination exceeds the path limit; nothing moved.')
    with connection.transaction():
        try_lock_root(connection, job['company_id'])
        if not connection.execute(
                "SELECT id FROM company_root_entry WHERE company_id=%s AND kind='folder' AND path=%s",
                (job['company_id'], DESTINATION_ROOT)).fetchone():
            raise InventoryError('Company Root / Mikan is missing; nothing moved.')
        for item in items:
            identifier = item['destination_entry_id']
            if not identifier or item['kind'] == 'version':
                continue
            new_path = f"{DESTINATION_ROOT}/{item['destination_path']}"
            parent, _, _name = new_path.rpartition('/')
            collision = connection.execute(
                'SELECT id FROM company_root_entry WHERE company_id=%s AND path=%s AND id<>%s',
                (job['company_id'], new_path, identifier),
            ).fetchone()
            if collision:
                raise InventoryError('Correct Mikan destination already exists; nothing moved.')
            connection.execute('UPDATE company_root_entry SET parent=%s WHERE id=%s AND company_id=%s',
                               (parent, identifier, job['company_id']))
        connection.execute("""UPDATE zoho_migration_item SET destination_path=%s || '/' || destination_path,
            updated_at=clock_timestamp() WHERE job_id=%s""", (DESTINATION_ROOT, job['id']))
        connection.execute('UPDATE zoho_migration_job SET destination_path=%s,updated_at=clock_timestamp() WHERE id=%s',
                           (new_root, job['id']))
    return [{**item, 'destination_path': f"{DESTINATION_ROOT}/{item['destination_path']}"} for item in items]


def prune_unstarted_current_version_duplicates(connection, reader, job, items):
    retained = list(items)
    for file_item in (item for item in items if item['kind'] == 'file'):
        versions = [item for item in items if item['kind'] == 'version' and item['source_file_id'] == file_item['source_id']]
        if not versions:
            continue
        _historical, current_version_id = historical_versions(
            reader_relationship(reader, file_item['source_id'], 'versions'),
            reader_relationship(reader, file_item['source_id'], 'approvedversions'), file_item,
        )
        candidates = [item for item in versions if item['source_version_id'] == current_version_id]
        if len(candidates) != 1:
            continue
        candidate = candidates[0]
        with connection.transaction():
            if candidate['destination_version_id']:
                deleted = connection.execute("""DELETE FROM company_root_version
                    WHERE id=%s AND company_id=%s AND state='pending' AND sha256 IS NULL RETURNING id""",
                    (candidate['destination_version_id'], job['company_id'])).fetchone()
                if not deleted:
                    continue
            deleted = connection.execute("""DELETE FROM zoho_migration_item
                WHERE id=%s AND job_id=%s AND state='pending' AND sha256 IS NULL RETURNING id""",
                (candidate['id'], job['id'])).fetchone()
            if not deleted:
                raise InventoryError('Current-version duplicate checkpoint changed during reconciliation.')
            retained.remove(candidate)
            connection.execute('''UPDATE zoho_migration_job SET versions_total=%s,bytes_total=%s,updated_at=clock_timestamp()
                WHERE id=%s''', (
                    sum(item['kind'] == 'version' for item in retained),
                    sum(item['size_bytes'] for item in retained if item['kind'] in ('file', 'version')), job['id']))
    return retained


def destination_storage(connection, company_id):
    rows = connection.execute('''SELECT storage.*,policy.max_file_bytes FROM company_upload_policy policy
        JOIN storage_connection storage ON storage.id=policy.connection_id
        WHERE policy.company_id=%s AND policy.enabled AND storage.enabled''', (company_id,)).fetchall()
    if len(rows) != 1:
        raise InventoryError('Migration requires exactly one enabled company storage destination.')
    return rows[0]


def reserve_items(connection, job, items):
    from insights import COMPANY_STORAGE_CAPACITY_BYTES

    with connection.transaction():
        try_lock_root(connection, job['company_id'])
        destination_root = connection.execute(
            f"SELECT id FROM {ROOT_ENTRIES} WHERE company_id=%s AND path=%s AND kind='folder'",
            (job['company_id'], DESTINATION_ROOT),
        ).fetchone()
        if not destination_root:
            create_root_folder(connection, job['company_id'], RootFolderInput(name=DESTINATION_ROOT), MIKAN_ROOT_ID)
        storage = destination_storage(connection, job['company_id'])
        files = [item for item in items if item['kind'] == 'file']
        versions = [item for item in items if item['kind'] == 'version']
        if any(item['size_bytes'] > min(storage['max_file_bytes'], 5_000_000_000) for item in [*files, *versions]):
            raise InventoryError('A source file or version exceeds the configured destination limit.')
        allocated = connection.execute(
            'SELECT coalesce(sum(greatest(storage_quota_bytes,storage_used_bytes)),0) AS bytes FROM team WHERE company_id=%s',
            (job['company_id'],),
        ).fetchone()['bytes']
        root_bytes = connection.execute(
            'SELECT coalesce(sum(size_bytes),0) AS bytes FROM company_root_entry WHERE company_id=%s',
            (job['company_id'],),
        ).fetchone()['bytes']
        history_bytes = connection.execute(
            "SELECT coalesce(sum(size_bytes),0) AS bytes FROM company_root_version WHERE company_id=%s AND state='ready'",
            (job['company_id'],),
        ).fetchone()['bytes']
        reserved = connection.execute(
            "SELECT coalesce(sum(size_bytes),0) AS bytes FROM zoho_migration_item WHERE job_id=%s AND kind IN ('file','version') AND destination_entry_id IS NULL AND destination_version_id IS NULL",
            (job['id'],),
        ).fetchone()['bytes']
        if allocated + root_bytes + history_bytes + reserved > COMPANY_STORAGE_CAPACITY_BYTES:
            raise InventoryError('Insufficient unallocated company storage; team allocations were not changed.')

        destination_files = {}
        for item in sorted(items, key=lambda value: (value['destination_path'].count('/'), value['destination_path'])):
            parent, _, name = item['destination_path'].rpartition('/')
            if item['kind'] == 'folder':
                existing = connection.execute(
                    'SELECT * FROM company_root_entry WHERE company_id=%s AND (path=%s OR source_id=%s)',
                    (job['company_id'], item['destination_path'], item['source_id']),
                ).fetchall()
                if existing:
                    if len(existing) != 1 or existing[0]['kind'] != 'folder' or existing[0]['path'] != item['destination_path'] or existing[0]['source_id'] != item['source_id']:
                        raise InventoryError('Destination folder collision; nothing overwritten.')
                    entry = existing[0]
                else:
                    entry = create_root_folder(connection, job['company_id'], RootFolderInput(name=name, parent=parent), item['source_id'])
                connection.execute(
                    "UPDATE zoho_migration_item SET destination_entry_id=%s,state='ready',completed_at=coalesce(completed_at,clock_timestamp()),updated_at=clock_timestamp() WHERE id=%s",
                    (entry['id'], item['id']),
                )
            elif item['kind'] == 'file':
                existing = connection.execute(
                    'SELECT * FROM company_root_entry WHERE company_id=%s AND (path=%s OR source_id=%s)',
                    (job['company_id'], item['destination_path'], item['source_id']),
                ).fetchall()
                if existing:
                    entry = existing[0]
                    if (len(existing) != 1 or entry['kind'] != 'file' or entry['path'] != item['destination_path']
                            or entry['source_id'] != item['source_id'] or entry['size_bytes'] != item['size_bytes']
                            or entry['source_modified_at'] != item['source_modified_at'] or entry['connection_id'] != storage['id']):
                        raise InventoryError('Destination file collision or changed snapshot; nothing overwritten.')
                else:
                    identifier = uuid4()
                    entry = connection.execute('''INSERT INTO company_root_entry
                        (id,company_id,kind,parent,name,source_id,source_modified_at,size_bytes,connection_id,object_key,state)
                        VALUES (%s,%s,'file',%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING *''',
                        (identifier, job['company_id'], parent, name, item['source_id'], item['source_modified_at'],
                         item['size_bytes'], storage['id'], f"mikan/company-root/{job['company_id']}/{identifier}"),
                    ).fetchone()
                destination_files[item['source_id']] = entry
                connection.execute(
                    'UPDATE zoho_migration_item SET destination_entry_id=%s,updated_at=clock_timestamp() WHERE id=%s',
                    (entry['id'], item['id']),
                )

        for item in versions:
            file = destination_files.get(item['source_file_id']) or connection.execute(
                "SELECT * FROM company_root_entry WHERE company_id=%s AND source_id=%s AND kind='file'",
                (job['company_id'], item['source_file_id']),
            ).fetchone()
            if not file:
                raise InventoryError('Version destination file is missing.')
            existing = connection.execute(
                'SELECT * FROM company_root_version WHERE file_id=%s AND source_version_id=%s',
                (file['id'], item['source_version_id']),
            ).fetchone()
            if existing:
                version = existing
                if (version['company_id'] != job['company_id'] or version['name'] != item['source_name']
                        or version['version_label'] != item['version_label'] or version['size_bytes'] != item['size_bytes']
                        or version['connection_id'] != storage['id']):
                    raise InventoryError('Destination version collision or changed snapshot; nothing overwritten.')
            else:
                identifier = uuid4()
                version = connection.execute('''INSERT INTO company_root_version
                    (id,file_id,company_id,source_version_id,version_label,source_created_at,source_modified_at,
                     connection_id,object_key,name,size_bytes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                    (identifier, file['id'], job['company_id'], item['source_version_id'], item['version_label'],
                     item['source_created_at'], item['source_modified_at'], storage['id'],
                     f"mikan/company-root/{job['company_id']}/versions/{identifier}", item['source_name'], item['size_bytes']),
                ).fetchone()
            connection.execute(
                'UPDATE zoho_migration_item SET destination_entry_id=%s,destination_version_id=%s,updated_at=clock_timestamp() WHERE id=%s',
                (file['id'], version['id'], item['id']),
            )
    return storage


def current_entry(item):
    metadata = item['source_metadata']
    return {
        **metadata,
        'id': item['source_id'], 'name': item['source_name'], 'parent_id': item['source_parent_id'],
        'is_folder': False, 'size_bytes': item['size_bytes'], 'modified_at': item['source_modified_at'],
        'path': item['destination_path'], 'native_kind': None,
    }


def historical_url(_reader, item):
    file_id = item['source_file_id']
    version_label = item['version_label']
    if (not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9]+', file_id)
            or not isinstance(version_label, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', version_label)):
        raise InventoryError('Invalid historical source identity; credentials were not sent.')
    url = httpx.URL(
        f'https://download-accl.zoho.in/v1/workdrive/download/{file_id}'
    ).copy_add_param('version', version_label)
    if (url.scheme != 'https' or url.host != 'download-accl.zoho.in' or url.port not in (None, 443)
            or url.userinfo or url.fragment
            or url.path != f'/v1/workdrive/download/{file_id}'
            or url.params.get('version') != version_label):
        raise InventoryError('Unverified historical download URL; credentials were not sent.')
    return url


def download_historical(reader, item, output):
    url = historical_url(reader, item)
    if not reader.access_token or time.monotonic() >= reader.expires_at:
        reader.refresh()
    for attempt in range(2):
        digest = hashlib.sha256()
        length = 0
        output.seek(0)
        output.truncate()
        with reader.client.stream('GET', url, follow_redirects=False,
                headers={'Authorization': f'Zoho-oauthtoken {reader.access_token}', 'Accept-Encoding': 'identity'}) as response:
            if response.status_code == 401 and not attempt:
                reader.refresh()
                continue
            if response.status_code != 200 or 'location' in response.headers:
                raise InventoryError(f'Zoho historical download HTTP {response.status_code}; migration stopped.')
            for chunk in response.iter_bytes(1024 * 1024):
                length += len(chunk)
                if length > item['size_bytes']:
                    raise InventoryError('Historical source exceeds its inventoried size.')
                output.write(chunk)
                digest.update(chunk)
        break
    if length != item['size_bytes']:
        raise InventoryError('Historical source size mismatch.')
    output.seek(0)
    return digest.hexdigest()


def verify_ready_checkpoint(client, storage, record, digest):
    if record['state'] != 'ready' or record['sha256'] != digest or not record['etag']:
        raise InventoryError('Verified destination checkpoint is incomplete.')
    head = object_head(client, storage, record)
    if not head or head['ContentLength'] != record['size_bytes'] or head['ETag'] != record['etag']:
        raise InventoryError('Verified destination object identity or size changed.')
    if record.get('object_version') and head.get('VersionId') != record['object_version']:
        raise InventoryError('Verified destination object version changed.')
    return head


def transfer_item(connection, reader, client, storage, job, item):
    table = 'company_root_entry' if item['kind'] == 'file' else 'company_root_version'
    identifier = item['destination_entry_id'] if item['kind'] == 'file' else item['destination_version_id']
    record = connection.execute(f'SELECT * FROM {table} WHERE id=%s AND company_id=%s',
        (identifier, job['company_id'])).fetchone()
    if not record:
        raise InventoryError('Reserved destination record is missing.')
    if record['state'] == 'ready':
        verify_ready_checkpoint(client, storage, record, record['sha256'])
        digest = record['sha256']
    else:
        digest = record['sha256']
        head = verify_object(client, storage, record, digest) if digest and object_head(client, storage, record) else None
        if head is None:
            if shutil.disk_usage(tempfile.gettempdir()).free < item['size_bytes'] + 128 * 1024 * 1024:
                raise InventoryError('Insufficient temporary disk space for the next item.')
            with tempfile.TemporaryFile() as output:
                digest = download_source(reader, current_entry(item), output) if item['kind'] == 'file' else download_historical(reader, item, output)
                with connection.transaction():
                    bound = connection.execute(
                        f"UPDATE {table} SET sha256=%s WHERE id=%s AND company_id=%s AND state='pending' AND (sha256 IS NULL OR sha256=%s) RETURNING id",
                        (digest, identifier, job['company_id'], digest),
                    ).fetchone()
                    if not bound:
                        raise InventoryError('Pending destination identity or state changed; nothing overwritten.')
                metadata = {'sha256': digest, 'zoho-source-id': item['source_file_id']}
                if item['kind'] == 'version':
                    metadata['zoho-source-version-id'] = item['source_version_id']
                client.put_object(Bucket=storage['bucket'], Key=record['object_key'], Body=output,
                    ContentLength=item['size_bytes'], ContentType='application/octet-stream', IfNoneMatch='*', Metadata=metadata)
                record['sha256'] = digest
                head = verify_object(client, storage, record, digest)
        with connection.transaction():
            published = connection.execute(
                f"UPDATE {table} SET state='ready',etag=%s,object_version=%s,uploaded_at=clock_timestamp() WHERE id=%s AND company_id=%s AND state='pending' AND sha256=%s RETURNING id",
                (head['ETag'], head.get('VersionId'), identifier, job['company_id'], digest),
            ).fetchone()
            if not published:
                raise InventoryError('Destination state changed before publication; rerun to reconcile.')
    with connection.transaction():
        connection.execute("""UPDATE zoho_migration_item SET state='ready',sha256=%s,last_error=NULL,
            completed_at=coalesce(completed_at,clock_timestamp()),updated_at=clock_timestamp() WHERE id=%s""", (digest, item['id']))
        connection.execute('''UPDATE zoho_migration_job SET
            files_complete=(SELECT count(*) FROM zoho_migration_item WHERE job_id=%s AND kind='file' AND state='ready'),
            versions_complete=(SELECT count(*) FROM zoho_migration_item WHERE job_id=%s AND kind='version' AND state='ready'),
            bytes_complete=(SELECT coalesce(sum(size_bytes),0) FROM zoho_migration_item WHERE job_id=%s AND kind IN ('file','version') AND state='ready'),
            updated_at=clock_timestamp() WHERE id=%s''', (job['id'], job['id'], job['id'], job['id']))


def persist_inventory(connection, job, items):
    with connection.transaction():
        if connection.execute('SELECT id FROM zoho_migration_item WHERE job_id=%s LIMIT 1', (job['id'],)).fetchone():
            return
        for item in items:
            connection.execute('''INSERT INTO zoho_migration_item
                (job_id,company_id,kind,source_id,source_parent_id,source_file_id,source_version_id,source_name,
                 destination_path,version_label,size_bytes,source_created_at,source_modified_at,source_metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (job['id'], job['company_id'], item['kind'], item['source_id'], item['source_parent_id'],
                 item['source_file_id'], item['source_version_id'], item['source_name'], item['destination_path'],
                 item['version_label'], item['size_bytes'], item['source_created_at'], item['source_modified_at'],
                 Jsonb(item['source_metadata'])))
        connection.execute('''UPDATE zoho_migration_job SET status='transferring',phase='Current files',
            folders_total=%s,folders_complete=0,files_total=%s,files_complete=0,versions_total=%s,versions_complete=0,
            bytes_total=%s,bytes_complete=0,started_at=coalesce(started_at,clock_timestamp()),updated_at=clock_timestamp()
            WHERE id=%s''', (
                sum(item['kind'] == 'folder' for item in items), sum(item['kind'] == 'file' for item in items),
                sum(item['kind'] == 'version' for item in items),
                sum(item['size_bytes'] for item in items if item['kind'] in ('file', 'version')), job['id']))


def execute_job(connection, job):
    credentials = migration_credentials()
    with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as http_client:
        reader = WorkDriveReader(http_client, credentials)
        items = connection.execute('SELECT * FROM zoho_migration_item WHERE job_id=%s ORDER BY destination_path,kind,source_id',
            (job['id'],)).fetchall()
        if not items:
            connection.execute("UPDATE zoho_migration_job SET status='inventory',phase='Reading source',attempts=attempts+1,updated_at=clock_timestamp() WHERE id=%s", (job['id'],))
            items = inventory_folder(reader, job['source_folder_id'], job['source_folder_name'])
            persist_inventory(connection, job, items)
            items = connection.execute('SELECT * FROM zoho_migration_item WHERE job_id=%s ORDER BY destination_path,kind,source_id',
                (job['id'],)).fetchall()
        items = repair_legacy_destination(connection, job, items)
        job = {**job, 'destination_path': f"{DESTINATION_ROOT}/{job['source_folder_name']}"}
        items = prune_unstarted_current_version_duplicates(connection, reader, job, items)
        storage = reserve_items(connection, job, items)
        items = connection.execute('SELECT * FROM zoho_migration_item WHERE job_id=%s ORDER BY destination_path,kind,source_id',
            (job['id'],)).fetchall()
        folders = [item for item in items if item['kind'] == 'folder']
        connection.execute("UPDATE zoho_migration_job SET folders_complete=%s,updated_at=clock_timestamp() WHERE id=%s", (len(folders), job['id']))
        client = storage_client(storage)
        try:
            client.head_bucket(Bucket=storage['bucket'])
            for kind, phase in (('file', 'Current files'), ('version', 'File versions')):
                connection.execute('UPDATE zoho_migration_job SET status=%s,phase=%s,updated_at=clock_timestamp() WHERE id=%s',
                    ('transferring', phase, job['id']))
                for item in (value for value in items if value['kind'] == kind):
                    transfer_item(connection, reader, client, storage, job, item)
            connection.execute("UPDATE zoho_migration_job SET status='verifying',phase='Final verification',updated_at=clock_timestamp() WHERE id=%s", (job['id'],))
            records = connection.execute('SELECT * FROM zoho_migration_item WHERE job_id=%s ORDER BY kind,destination_path', (job['id'],)).fetchall()
            if len(records) != len(items) or any(item['state'] != 'ready' for item in records):
                raise InventoryError('Final checkpoint reconciliation failed.')
            for item in records:
                if item['kind'] in ('file', 'version'):
                    table = 'company_root_entry' if item['kind'] == 'file' else 'company_root_version'
                    identifier = item['destination_entry_id'] if item['kind'] == 'file' else item['destination_version_id']
                    record = connection.execute(f'SELECT * FROM {table} WHERE id=%s AND company_id=%s AND state=\'ready\'',
                        (identifier, job['company_id'])).fetchone()
                    if not record:
                        raise InventoryError('Final destination record is missing.')
                    verify_ready_checkpoint(client, storage, record, item['sha256'])
            with connection.transaction():
                connection.execute("""UPDATE zoho_migration_job SET status='complete',phase='Complete',last_error=NULL,
                    completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s""", (job['id'],))
                connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
                    (job['company_id'], 'Zoho migration', 'root_import_complete', job['destination_path'],
                     json.dumps({'folders': len(folders), 'files': sum(item['kind'] == 'file' for item in items),
                                 'versions': sum(item['kind'] == 'version' for item in items),
                                 'bytes': sum(item['size_bytes'] for item in items if item['kind'] in ('file', 'version'))})))
        finally:
            client.close()


def process_migrations():
    with connect() as connection:
        connection.autocommit = True
        job = connection.execute("""SELECT * FROM zoho_migration_job
            WHERE status IN ('queued','inventory','transferring','verifying')
            ORDER BY updated_at,id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if not job:
            return
        lock_key = f"zoho-migration:{job['company_id']}:{job['source_folder_id']}"
        if not connection.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired', (lock_key,)).fetchone()['acquired']:
            return
        try:
            execute_job(connection, job)
        except MigrationDeferred as error:
            LOGGER.info('Zoho migration job %s deferred: %s', job['id'], error)
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, str) else 'Migration stopped by destination validation.'
            LOGGER.warning('Zoho migration job %s stopped (HTTP %s): %s', job['id'], error.status_code, detail)
            connection.execute("""UPDATE zoho_migration_job SET status='failed',phase='Stopped',last_error=%s,
                updated_at=clock_timestamp() WHERE id=%s""", (detail, job['id']))
        except (InventoryError, httpx.HTTPError, OSError, ValueError, BotoCoreError, ClientError) as error:
            detail = str(error) if isinstance(error, InventoryError) else 'Migration interrupted by a provider or storage error. Retry resumes verified items.'
            LOGGER.warning('Zoho migration job %s failed (%s)', job['id'], type(error).__name__)
            connection.execute("""UPDATE zoho_migration_job SET status='failed',phase='Stopped',last_error=%s,
                updated_at=clock_timestamp() WHERE id=%s""", (detail, job['id']))
        finally:
            connection.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (lock_key,))


def create_migration_router(company_dependency, origin_dependency):
    router = APIRouter(prefix='/company/teams/data/migration', tags=['company-data-migration'])

    @router.get('')
    def migration_folders(admin=Depends(company_dependency), connection=Depends(get_db)):
        jobs = connection.execute(
            f'SELECT {JOB_COLUMNS} FROM zoho_migration_job WHERE company_id=%s AND source_root_id=%s',
            (admin['company_id'], MIKAN_ROOT_ID),
        ).fetchall()
        by_source = {row['source_folder_id']: job_dict(row) for row in jobs}
        active = [row for row in jobs if row['status'] in ('queued', 'inventory', 'transferring', 'verifying')]
        if active:
            return {'items': [{
                'source_folder_id': row['source_folder_id'], 'name': row['source_folder_name'],
                'size_bytes': row['bytes_total'], 'job': job_dict(row),
            } for row in jobs], 'source_unavailable': False}
        try:
            folders = list_source_folders()
        except (InventoryError, httpx.HTTPError, OSError, ValueError) as error:
            if not jobs:
                raise HTTPException(503, 'Zoho folders are temporarily unavailable.') from error
            return {'items': [{
                'source_folder_id': row['source_folder_id'], 'name': row['source_folder_name'],
                'size_bytes': row['bytes_total'], 'job': job_dict(row),
            } for row in jobs], 'source_unavailable': True}
        return {'items': [{**folder, 'job': by_source.get(folder['source_folder_id'])} for folder in folders],
                'source_unavailable': False}

    @router.post('/jobs', status_code=202, dependencies=[Depends(origin_dependency)])
    def start_migration(payload: MigrationStart, admin=Depends(company_dependency), connection=Depends(get_db)):
        try:
            folders = {folder['source_folder_id']: folder for folder in list_source_folders()}
        except (InventoryError, httpx.HTTPError, OSError, ValueError) as error:
            raise HTTPException(503, 'Zoho folders are temporarily unavailable.') from error
        folder = folders.get(payload.source_folder_id)
        if not folder:
            raise HTTPException(404, 'Zoho folder not found under Mikan.')
        existing = select_job(connection, admin['company_id'], payload.source_folder_id)
        if existing:
            return job_dict(existing)
        with connection.transaction():
            job = connection.execute(
                f'''INSERT INTO zoho_migration_job
                    (company_id,source_root_id,source_folder_id,source_folder_name,destination_path)
                    VALUES (%s,%s,%s,%s,%s) RETURNING {JOB_COLUMNS}''',
                (admin['company_id'], MIKAN_ROOT_ID, folder['source_folder_id'], folder['name'],
                 f"{DESTINATION_ROOT}/{folder['name']}"),
            ).fetchone()
        return job_dict(job)

    @router.post('/jobs/{identifier}/retry', status_code=202, dependencies=[Depends(origin_dependency)])
    def retry_migration(identifier: UUID, admin=Depends(company_dependency), connection=Depends(get_db)):
        with connection.transaction():
            job = connection.execute(
                f'''UPDATE zoho_migration_job SET status='queued',phase='Queued for retry',last_error=NULL,
                    completed_at=NULL,updated_at=clock_timestamp()
                    WHERE id=%s AND company_id=%s AND status='failed' RETURNING {JOB_COLUMNS}''',
                (identifier, admin['company_id']),
            ).fetchone()
        if not job:
            raise HTTPException(409, 'Only a failed migration can be retried.')
        return job_dict(job)

    return router