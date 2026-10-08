import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import test_company_root
import zoho_migrate_general_version as migration
from zoho_inventory import InventoryError, WorkDriveReader
from zoho_migrate_general_version import (
    SOURCE_FILE_ID, SOURCE_VERSION_ID, VERSION_SHA256, VERSION_SIZE,
    download_version, historical_url, migrate,
)
from test_zoho_migrate import MemoryStorage


def exact_pdf():
    prefix = b'%PDF-1.4\n'
    seed = hashlib.sha256(b'mikan-general-version-1').digest()
    body = (seed * ((VERSION_SIZE - len(prefix) + len(seed) - 1) // len(seed)))[:VERSION_SIZE - len(prefix)]
    return prefix + body


class HistoricalVersionSourceTests(unittest.TestCase):
    def test_execute_rejects_missing_credentials_before_database_access(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / '.env.zoho'
            output = io.StringIO()
            with patch('sys.stdout', output), patch.object(migration, 'migration_dsn') as migration_dsn:
                result = migration.main([
                    '--execute', '--confirm-company', 'Mikan Engineering Pvt Ltd', '--backup-confirmed',
                    '--credentials', str(missing),
                ])
        self.assertEqual(result, 1)
        migration_dsn.assert_not_called()
        self.assertIn(f'Zoho credentials file is missing: {missing}', output.getvalue())

    def test_execute_uses_complete_environment_credentials_before_database_access(self):
        credentials = {
            'ZOHO_CLIENT_ID': 'client',
            'ZOHO_CLIENT_SECRET': 'secret',
            'ZOHO_REFRESH_TOKEN': 'refresh',
            'ZOHO_ACCOUNTS_URL': 'https://accounts.zoho.in',
        }
        with patch.dict(os.environ, credentials, clear=True):
            self.assertEqual(migration.migration_credentials(), credentials)

    def test_partial_environment_credentials_fail_before_database_access(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'ZOHO_CLIENT_ID': 'client'}, clear=True), \
                patch('sys.stdout', output), patch.object(migration, 'migration_dsn') as migration_dsn:
            result = migration.main([
                '--execute', '--confirm-company', 'Mikan Engineering Pvt Ltd', '--backup-confirmed',
            ])
        self.assertEqual(result, 1)
        migration_dsn.assert_not_called()
        self.assertIn('Missing permanent Zoho environment variable: ZOHO_CLIENT_SECRET', output.getvalue())

    def reader(self, content=None, status=200, download_url=None, attributes=None):
        content = exact_pdf() if content is None else content
        download_url = download_url or f'https://download-accl.zoho.in/v1/workdrive/previewdata/{SOURCE_FILE_ID}?version=4017837000024806802'
        attributes = attributes or {'preview_data_url': download_url, 'size': VERSION_SIZE}

        def handle(request):
            if request.method == 'POST':
                return httpx.Response(200, json={'access_token': 'test-token', 'api_domain': 'https://www.zohoapis.in'})
            if request.url.host == 'www.zohoapis.in':
                self.assertEqual(request.url.path, f'/workdrive/api/v1/versions/{SOURCE_VERSION_ID}/previewinfo')
                return httpx.Response(200, json={'data': {'attributes': attributes}})
            return httpx.Response(status, content=content)

        client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False)
        self.addCleanup(client.close)
        return WorkDriveReader(client, {'ZOHO_CLIENT_ID': 'test', 'ZOHO_CLIENT_SECRET': 'test', 'ZOHO_REFRESH_TOKEN': 'test'})

    def test_exact_historical_source_download(self):
        content = exact_pdf()
        self.assertNotEqual(hashlib.sha256(content).hexdigest(), VERSION_SHA256)
        reader = self.reader(content=content)
        output = io.BytesIO()
        with unittest.mock.patch('zoho_migrate_general_version.VERSION_SHA256', hashlib.sha256(content).hexdigest()):
            self.assertEqual(download_version(reader, output), hashlib.sha256(content).hexdigest())
        self.assertEqual(output.read(), content)
        self.assertEqual(reader.requests, 1)

    def test_untrusted_url_redirect_size_and_hash_are_rejected(self):
        cases = (
            self.reader(download_url='https://untrusted.invalid/file?version=1'),
            self.reader(status=302),
            self.reader(content=exact_pdf()[:-1]),
            self.reader(content=b'%PDF-1.4\n' + b'x' * (VERSION_SIZE - 9)),
        )
        for reader in cases:
            with self.subTest(reader=reader), self.assertRaises(InventoryError):
                download_version(reader, io.BytesIO())

    def test_version_preview_method_rejects_other_identifier(self):
        reader = self.reader()
        with self.assertRaises(InventoryError):
            reader.version_preview_info('../other')
        self.assertIsInstance(historical_url(reader), httpx.URL)

    def test_formatted_preview_size_uses_verified_binary_length(self):
        url = f'https://download-accl.zoho.in/v1/workdrive/previewdata/{SOURCE_FILE_ID}?version=4017837000024806802'
        reader = self.reader(attributes={'preview_data_url': url, 'size': '503.56 KB'})
        self.assertIsInstance(historical_url(reader), httpx.URL)


@unittest.skipUnless(os.environ.get('MIKAN_TREE_TEST_SOCKET'), 'Explicit scratch PostgreSQL socket required')
class HistoricalVersionDatabaseTests(unittest.TestCase):
    tearDown = test_company_root.CompanyRootDatabaseTests.tearDown

    def setUp(self):
        test_company_root.CompanyRootDatabaseTests.setUp(self)
        self.connection.execute('UPDATE company SET name=%s WHERE id=%s', ('Mikan Engineering Pvt Ltd', self.company))
        self.connection.execute('INSERT INTO company_upload_policy(company_id,connection_id,enabled) VALUES (%s,%s,true)',
            (self.company, self.storage))
        self.current = self.connection.execute("""INSERT INTO company_root_entry(company_id,kind,parent,name,source_id,
            source_modified_at,size_bytes,connection_id,object_key,etag,sha256,uploaded_at)
            VALUES (%s,'file','General','VALVE PIT GA DRAWING.pdf',%s,'current',1494625,%s,%s,'current',%s,clock_timestamp()) RETURNING *""",
            (self.company, SOURCE_FILE_ID, self.storage, str(self.current_storage_key()), 'a' * 64)).fetchone()
        self.storage_client = MemoryStorage()

    @staticmethod
    def current_storage_key():
        from uuid import uuid4
        return uuid4()

    def reader(self, content):
        def handle(request):
            if request.method == 'POST':
                return httpx.Response(200, json={'access_token': 'test-token', 'api_domain': 'https://www.zohoapis.in'})
            if request.url.host == 'www.zohoapis.in':
                return httpx.Response(200, json={'data': {'attributes': {
                    'preview_data_url': f'https://download-accl.zoho.in/v1/workdrive/previewdata/{SOURCE_FILE_ID}?version=4017837000024806802',
                    'size': VERSION_SIZE,
                }}})
            return httpx.Response(200, content=content)

        client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False)
        self.addCleanup(client.close)
        return WorkDriveReader(client, {'ZOHO_CLIENT_ID': 'test', 'ZOHO_CLIENT_SECRET': 'test', 'ZOHO_REFRESH_TOKEN': 'test'})

    def test_full_publish_and_idempotent_rerun_preserve_team_quota(self):
        content = exact_pdf()
        digest = hashlib.sha256(content).hexdigest()
        reader = self.reader(content=content)
        with patch('zoho_migrate_general_version.VERSION_SHA256', digest), \
                patch('uploads.storage_client', return_value=self.storage_client):
            migrate(self.connection, reader)
            first = self.connection.execute('SELECT * FROM company_root_version WHERE file_id=%s', (self.current['id'],)).fetchone()
            migrate(self.connection, reader)
            second = self.connection.execute('SELECT * FROM company_root_version WHERE file_id=%s', (self.current['id'],)).fetchone()
        self.assertEqual(first['state'], 'ready')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['sha256'], digest)
        self.assertEqual(self.storage_client.puts, 1)
        self.assertEqual(reader.requests, 1)
        team = self.connection.execute('SELECT storage_quota_bytes,storage_used_bytes FROM team WHERE id=%s', (self.team,)).fetchone()
        self.assertEqual(team, {'storage_quota_bytes': 5_000_000_000_000, 'storage_used_bytes': 0})
        current = self.connection.execute('SELECT size_bytes,etag,sha256 FROM company_root_entry WHERE id=%s', (self.current['id'],)).fetchone()
        self.assertEqual(current, {'size_bytes': 1_494_625, 'etag': 'current', 'sha256': 'a' * 64})


if __name__ == '__main__':
    unittest.main()