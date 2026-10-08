import unittest
import tempfile
from pathlib import Path

import httpx

from zoho_inventory import (
    InventoryError, WorkDriveReader, children, initialize_roots,
    inventory_summary, open_inventory, scan_folder,
)


class WorkDriveReaderTests(unittest.TestCase):
    def test_only_token_post_and_metadata_get_with_private_headers(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.host == 'accounts.zoho.in':
                self.assertEqual(request.method, 'POST')
                self.assertNotIn('authorization', request.headers)
                self.assertIn(b'grant_type=refresh_token', request.content)
                return httpx.Response(200, json={'access_token': 'fake-token', 'api_domain': 'https://www.zohoapis.in'})
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['authorization'], 'Zoho-oauthtoken fake-token')
            return httpx.Response(200, json={'data': []})
        credentials = {'ZOHO_CLIENT_ID': 'fake-id', 'ZOHO_CLIENT_SECRET': 'fake-secret', 'ZOHO_REFRESH_TOKEN': 'fake-refresh'}
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            reader = WorkDriveReader(client, credentials)
            self.assertEqual(reader.get('/teamfolders/abc/files'), {'data': []})
            reader.get('/files/def/files')
            with self.assertRaises(InventoryError):
                reader.get('/files/def/download')
        self.assertEqual(len(calls), 3)
        self.assertEqual(reader.requests, 2)

    def test_untrusted_domain_rejected_before_sending_token(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={'access_token': 'fake-token', 'api_domain': 'https://example.com'})
        credentials = {'ZOHO_CLIENT_ID': 'fake-id', 'ZOHO_CLIENT_SECRET': 'fake-secret', 'ZOHO_REFRESH_TOKEN': 'fake-refresh'}
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(InventoryError):
                WorkDriveReader(client, credentials).get('/teamfolders/abc')
        self.assertEqual(len(calls), 1)


class InventoryTraversalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'private'
        self.connection = open_inventory(self.directory)
        self.addCleanup(self.connection.close)
        initialize_roots(self.connection, [{
            'id': 'root', 'name': 'General', 'reported_storage': {}, 'partial_access': False,
        }])

    def record(self, resource_id, parent, folder=False, size=12):
        return {'id': resource_id, 'attributes': {
            'name': f'Exact / name {resource_id}', 'parent_id': parent, 'is_folder': folder,
            'storage_info': {'size_in_bytes': size}, 'download_url': 'DO-NOT-STORE',
        }}

    def test_pagination_continues_until_empty_and_repeated_ids_fail(self):
        class Reader:
            def __init__(self):
                self.offsets = []
            def get(self, path, params):
                self.offsets.append(params['page[offset]'])
                if params['page[offset]'] == 0:
                    return {'data': [{'id': f'file{index}'} for index in range(50)]}
                if params['page[offset]'] == 50:
                    return {'data': [{'id': 'last'}]}
                return {'data': []}
        reader = Reader()
        self.assertEqual(len(list(children(reader, '/files/root/files'))), 51)
        self.assertEqual(reader.offsets, [0, 50, 51])
        reader.get = lambda path, params: {'data': [{'id': 'repeated'}]}
        with self.assertRaises(InventoryError):
            list(children(reader, '/files/root/files'))

    def test_hierarchy_empty_folders_native_sizes_and_resume(self):
        records = [self.record('empty', 'root', True), self.record('native', 'root', size=None), self.record('file', 'root', size='42')]
        class Reader:
            def get(self, path, params):
                if params['page[offset]'] or path == '/files/empty/files':
                    return {'data': []}
                if params['filter[type]'] == 'all':
                    return {'data': records}
                if params['filter[type]'] == 'documents_native':
                    return {'data': [records[1]]}
                return {'data': []}
        scan_folder(Reader(), self.connection, self.connection.execute('SELECT * FROM entries WHERE id="root"').fetchone())
        summary = inventory_summary(self.connection)[0]
        self.assertEqual(summary['files_seen'], 2)
        self.assertEqual(summary['folders_pending'], 1)
        self.assertEqual(summary['known_file_bytes'], 42)
        self.assertEqual(summary['files_missing_size'], 1)
        self.assertEqual(summary['native_documents_seen'], 1)
        scan_folder(Reader(), self.connection, self.connection.execute('SELECT * FROM entries WHERE id="empty"').fetchone())
        self.assertEqual(inventory_summary(self.connection)[0]['status'], 'accessible_tree_traversed')
        reopened = open_inventory(self.directory)
        self.addCleanup(reopened.close)
        self.assertEqual(inventory_summary(reopened)[0]['files_seen'], 2)
        self.assertEqual(reopened.execute('SELECT name FROM entries WHERE id="file"').fetchone()[0], 'Exact / name file')
        self.assertNotIn(b'DO-NOT-STORE', (self.directory / 'inventory.sqlite3').read_bytes())
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.directory / 'inventory.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_failed_listing_keeps_folder_pending_without_partial_children(self):
        record = self.record('first', 'root')
        class Reader:
            def get(self, path, params):
                if params['page[offset]']:
                    raise InventoryError('HTTP failure')
                return {'data': [record]}
        with self.assertRaises(InventoryError):
            scan_folder(Reader(), self.connection, self.connection.execute('SELECT * FROM entries').fetchone())
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 1)
        self.assertEqual(inventory_summary(self.connection)[0]['folders_pending'], 1)

    def test_duplicate_cycle_rolls_back_entire_folder(self):
        record = self.record('root', 'root', True)
        class Reader:
            def get(self, path, params):
                return {'data': [] if params['page[offset]'] else [record]}
        with self.assertRaises(InventoryError):
            scan_folder(Reader(), self.connection, self.connection.execute('SELECT * FROM entries').fetchone())
        self.assertEqual(inventory_summary(self.connection)[0]['folders_pending'], 1)


if __name__ == '__main__':
    unittest.main()