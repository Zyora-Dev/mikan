import argparse
import json
import os
import re
import sqlite3
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from zoho_authorize import ACCOUNTS_URL, CREDENTIALS_PATH, AuthorizationError, load_credentials


ROOT_IDS = (
    '63sop6f03b65dd0784c0ba4a7f75e5ceb86f6',
    '4ligq5e352ffc509e405c863fc17a7c4d83c1',
)
REPORT_DIRECTORY = Path(__file__).with_name('.zoho-inventory')
NATIVE_FILTERS = ('documents_native', 'spreadsheets_native', 'presentations_native')
METADATA_ATTEMPTS = 4


class InventoryError(Exception):
    pass


class TransientProviderError(OSError):
    pass


def response_json(response, context='API request'):
    if response.status_code != 200:
        if context == 'OAuth token request' and response.status_code == 400:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and payload.get('error') == 'Access Denied':
                raise InventoryError(
                    'Zoho OAuth access-token limit reached; wait at least 10 minutes before retrying.'
                )
        raise InventoryError(
            f'Zoho {context} returned HTTP {response.status_code}; no retry made. Response content hidden.'
        )
    try:
        payload = response.json()
    except ValueError:
        raise InventoryError('Zoho returned invalid JSON; response content hidden.') from None
    if not isinstance(payload, dict) or payload.get('error') or payload.get('errors'):
        raise InventoryError('Zoho returned an API error or unexpected response; content hidden.')
    return payload


class WorkDriveReader:
    def __init__(self, client, credentials):
        self.client = client
        self.credentials = credentials
        self.access_token = None
        self.api_domain = None
        self.expires_at = 0
        self.requests = 0

    def refresh(self):
        payload = response_json(self.client.post(
            f'{ACCOUNTS_URL}/oauth/v2/token',
            data={
                'client_id': self.credentials['ZOHO_CLIENT_ID'],
                'client_secret': self.credentials['ZOHO_CLIENT_SECRET'],
                'refresh_token': self.credentials['ZOHO_REFRESH_TOKEN'],
                'grant_type': 'refresh_token',
            },
        ), 'OAuth token request')
        token = payload.get('access_token')
        domain = payload.get('api_domain')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9._~-]+', token):
            raise InventoryError('Zoho did not return a valid access token.')
        if domain != 'https://www.zohoapis.in':
            raise InventoryError('Unexpected API domain; refusing to send credentials outside the India API host.')
        self.access_token = token
        self.api_domain = domain
        self.expires_at = time.monotonic() + 3000

    def authorized_get(self, path, params=None):
        if not self.access_token or time.monotonic() >= self.expires_at:
            self.refresh()
        refreshed = False
        for attempt in range(1, METADATA_ATTEMPTS + 1):
            try:
                response = self.client.get(
                    f'{self.api_domain}/workdrive/api/v1{path}',
                    params=params,
                    headers={'Authorization': f'Zoho-oauthtoken {self.access_token}'},
                )
            except httpx.TransportError:
                if attempt == METADATA_ATTEMPTS:
                    raise
                time.sleep(2 ** (attempt - 1))
                continue
            self.requests += 1
            if response.status_code == 401 and not refreshed:
                self.refresh()
                refreshed = True
                continue
            if (response.status_code in (408, 425, 429) or response.status_code >= 500) and attempt < METADATA_ATTEMPTS:
                time.sleep(2 ** (attempt - 1))
                continue
            if response.status_code in (408, 425, 429) or response.status_code >= 500:
                raise TransientProviderError(
                    f'Zoho metadata temporarily returned HTTP {response.status_code}.')
            return response_json(response)
        raise InventoryError('Zoho metadata retry did not complete.')

    def get(self, path, params=None):
        if not re.fullmatch(r'/(teamfolders|files)/[A-Za-z0-9]+(?:/files)?', path):
            raise InventoryError('Only source metadata and folder listing endpoints are allowed.')
        return self.authorized_get(path, params)

    def version_preview_info(self, version_id):
        if not re.fullmatch(r'[A-Za-z0-9]+-[0-9]+', version_id):
            raise InventoryError('Invalid source version identifier.')
        return self.authorized_get(f'/versions/{version_id}/previewinfo')


def source_roots(reader):
    roots = []
    for root_id in ROOT_IDS:
        payload = reader.get(f'/teamfolders/{root_id}')
        root = payload.get('data')
        if not isinstance(root, dict) or not isinstance(root.get('attributes'), dict):
            raise InventoryError('Unexpected Team Folder metadata shape.')
        attributes = root['attributes']
        name = attributes.get('name')
        if name not in ('General', 'Mikan'):
            raise InventoryError('A source root is not named General or Mikan; stopped before traversal.')
        if root.get('id') != root_id:
            raise InventoryError('Team Folder ID does not match the requested source.')
        roots.append({
            'id': root_id, 'name': name,
            'reported_storage': attributes.get('storage_info', {}),
            'partial_access': attributes.get('is_partial_lib'),
        })
    if {root['name'] for root in roots} != {'General', 'Mikan'}:
        raise InventoryError('Both distinct source roots must be General and Mikan.')
    return roots


def children(reader, path, resource_filter='all'):
    offset = 0
    seen = set()
    while True:
        payload = reader.get(path, {
            'page[limit]': 50, 'page[offset]': offset,
            'filter[type]': resource_filter, 'sort': 'name',
        })
        records = payload.get('data')
        if not isinstance(records, list):
            raise InventoryError('Unexpected folder listing shape.')
        if not records:
            return
        for record in records:
            if not isinstance(record, dict):
                raise InventoryError('Unexpected resource metadata shape.')
            resource_id = record.get('id')
            if not isinstance(resource_id, str) or not re.fullmatch(r'[A-Za-z0-9]+', resource_id):
                raise InventoryError('Missing or unsafe source resource ID.')
            if resource_id in seen:
                raise InventoryError('Repeated resource/page detected; source may have changed during listing.')
            seen.add(resource_id)
            yield record
        offset += len(records)


def open_inventory(directory):
    directory.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise InventoryError('Inventory directory must be an owner-only directory with permissions 700.')
    path = directory / 'inventory.sqlite3'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise InventoryError('Inventory database must be an owner-only regular file with permissions 600.')
    finally:
        os.close(descriptor)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS roots (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, reported TEXT NOT NULL,
            partial_access INTEGER, checked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY, root_id TEXT NOT NULL, parent_id TEXT,
            name TEXT NOT NULL, is_folder INTEGER NOT NULL, size_bytes INTEGER,
            native_kind TEXT, service_type TEXT, extension TEXT, resource_type TEXT,
            modified_at TEXT, scanned INTEGER NOT NULL DEFAULT 0,
            observed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS pending_folders ON entries(is_folder, scanned);
        CREATE INDEX IF NOT EXISTS root_entries ON entries(root_id);
    ''')
    return connection


def initialize_roots(connection, roots):
    now = datetime.now(timezone.utc).isoformat()
    with connection:
        for root in roots:
            connection.execute('''
                INSERT INTO roots VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, reported=excluded.reported,
                    partial_access=excluded.partial_access, checked_at=excluded.checked_at
            ''', (root['id'], root['name'], json.dumps(root['reported_storage']), root['partial_access'], now))
            connection.execute('''
                INSERT OR IGNORE INTO entries
                    (id, root_id, parent_id, name, is_folder, observed_at)
                VALUES (?, ?, NULL, ?, 1, ?)
            ''', (root['id'], root['id'], root['name'], now))


def scan_folder(reader, connection, folder):
    namespace = 'teamfolders' if folder['parent_id'] is None else 'files'
    path = f"/{namespace}/{folder['id']}/files"
    records = list(children(reader, path))
    file_ids = set()
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for record in records:
        attributes = record.get('attributes')
        if not isinstance(attributes, dict):
            raise InventoryError('Missing file attributes.')
        name, is_folder = attributes.get('name'), attributes.get('is_folder')
        if not isinstance(name, str) or type(is_folder) is not bool:
            raise InventoryError('Missing file name or folder flag; no folder checkpoint saved.')
        if attributes.get('parent_id') != folder['id']:
            raise InventoryError('Unexpected parent ID; source may have moved during inventory.')
        storage = attributes.get('storage_info') or {}
        size = storage.get('size_in_bytes') if isinstance(storage, dict) else None
        if isinstance(size, str) and size.isascii() and size.isdigit():
            size = int(size)
        if type(size) is not int or size < 0 or size > 9223372036854775807:
            size = None
        if not is_folder:
            file_ids.add(record['id'])
        rows.append((
            record['id'], folder['root_id'], folder['id'], name, int(is_folder),
            None if is_folder else size, None, attributes.get('service_type'), attributes.get('extn'),
            str(attributes.get('resource_type', '')), str(attributes.get('modified_time_in_millisecond', '')),
            0, now,
        ))
    native = {}
    if file_ids:
        for resource_filter in NATIVE_FILTERS:
            for record in children(reader, path, resource_filter):
                if record['id'] not in file_ids or record['id'] in native:
                    raise InventoryError('Native-document filters disagree with folder contents; checkpoint not saved.')
                native[record['id']] = resource_filter
    try:
        with connection:
            connection.executemany('INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
            for resource_id, kind in native.items():
                connection.execute('UPDATE entries SET native_kind=? WHERE id=?', (kind, resource_id))
            connection.execute('UPDATE entries SET scanned=1 WHERE id=?', (folder['id'],))
    except sqlite3.IntegrityError:
        raise InventoryError('Duplicate or cyclic resource detected; current folder checkpoint rolled back.') from None


def inventory_summary(connection):
    results = []
    for root in connection.execute('SELECT * FROM roots ORDER BY name'):
        counts = dict(connection.execute('''
            SELECT SUM(CASE WHEN is_folder=0 THEN 1 ELSE 0 END) AS files_seen,
                SUM(CASE WHEN is_folder=1 AND parent_id IS NOT NULL THEN 1 ELSE 0 END) AS folders_seen,
                SUM(CASE WHEN is_folder=1 AND scanned=0 THEN 1 ELSE 0 END) AS folders_pending,
                SUM(CASE WHEN is_folder=1 AND scanned=1 THEN 1 ELSE 0 END) AS folders_scanned,
                COALESCE(SUM(CASE WHEN is_folder=0 THEN size_bytes ELSE 0 END), 0) AS known_file_bytes,
                SUM(CASE WHEN is_folder=0 AND size_bytes IS NULL THEN 1 ELSE 0 END) AS files_missing_size,
                SUM(CASE WHEN native_kind IS NOT NULL THEN 1 ELSE 0 END) AS native_documents_seen
            FROM entries WHERE root_id=?
        ''', (root['id'],)).fetchone())
        counts['native_by_kind'] = dict(connection.execute('''
            SELECT native_kind, COUNT(*) FROM entries
            WHERE root_id=? AND native_kind IS NOT NULL GROUP BY native_kind
        ''', (root['id'],)))
        results.append({
            'name': root['name'], 'source_id': root['id'],
            'zoho_reported_storage': json.loads(root['reported']),
            'partial_access': root['partial_access'],
            'status': 'partial' if counts['folders_pending'] or root['partial_access'] else 'accessible_tree_traversed',
            **counts,
        })
    return results


def run_inventory(reader, directory, max_folders):
    connection = open_inventory(directory)
    try:
        roots = source_roots(reader)
        initialize_roots(connection, roots)
        for root in roots:
            print(json.dumps({'name': root['name'], 'zoho_reported_storage': root['reported_storage'], 'partial_access': root['partial_access']}), flush=True)
        processed = 0
        while max_folders == 0 or processed < max_folders:
            folder = connection.execute('''
                SELECT * FROM entries WHERE is_folder=1 AND scanned=0
                ORDER BY rowid LIMIT 1
            ''').fetchone()
            if folder is None:
                break
            scan_folder(reader, connection, folder)
            processed += 1
            if processed % 10 == 0:
                print(f'Checkpoint: {processed} folders scanned this run; {reader.requests} metadata requests.', flush=True)
        print(json.dumps(inventory_summary(connection), indent=2), flush=True)
        print(f'Private resumable inventory: {directory / "inventory.sqlite3"}', flush=True)
        print('No file contents downloaded, migrated, assigned, or changed.', flush=True)
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only inventory of the General and Mikan WorkDrive Team Folders.')
    parser.add_argument('--probe', action='store_true', help='Read root names and reported totals only.')
    parser.add_argument('--max-folders', type=int, default=100, help='Folder checkpoints per run (default 100; 0 continues until done).')
    args = parser.parse_args(argv)
    if args.max_folders < 0:
        parser.error('--max-folders must be zero or greater')
    try:
        credentials, _ = load_credentials(CREDENTIALS_PATH, require_refresh=True)
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            reader = WorkDriveReader(client, credentials)
            if args.probe:
                print(json.dumps(source_roots(reader), indent=2), flush=True)
            else:
                run_inventory(reader, REPORT_DIRECTORY, args.max_folders)
        return 0
    except (AuthorizationError, InventoryError) as error:
        print(f'Inventory stopped: {error}', file=sys.stderr)
    except httpx.RequestError:
        print('Inventory stopped: network request failed; no retry made. Details hidden.', file=sys.stderr)
    except (OSError, UnicodeError):
        print('Inventory stopped: private local configuration or report could not be accessed.', file=sys.stderr)
    except sqlite3.Error:
        print('Inventory stopped: local inventory database error; completed checkpoints retained.', file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print('Inventory cancelled.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())