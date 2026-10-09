import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from botocore.exceptions import ClientError

import test_company_root
from zoho_inventory import InventoryError, TransientProviderError, WorkDriveReader, initialize_roots, open_inventory
from zoho_migrate import (
    COMPANY_NAME, GENERAL_ID, destination_context, download_source, load_plan,
    main, migrate, migration_dsn, plan_summary, reserve_destination, verify_source,
)


def sample_plan():
    records = [
        (GENERAL_ID, None, 'General', True, ''),
        ('empty', GENERAL_ID, 'Empty folder', True, 'General'),
        ('nested', GENERAL_ID, 'Engineering', True, 'General'),
        ('file1', GENERAL_ID, 'Drawing 01.pdf', False, 'General'),
        ('file2', 'nested', 'Plan.dwg', False, 'General/Engineering'),
    ]
    return [dict(id=identifier, root_id=GENERAL_ID, parent_id=parent_id, name=name,
        is_folder=int(folder), parent=parent, path=f'{parent}/{name}' if parent else name,
        size_bytes=None if folder else 7, modified_at='100', native_kind=None, scanned=int(folder))
        for identifier, parent_id, name, folder, parent in records]


class SourceFixture:
    def source_reader(self, plan):
        self.source_records = {entry['id']: {'id': entry['id'], 'attributes': {
            'name': entry['name'], 'parent_id': entry['parent_id'], 'is_folder': bool(entry['is_folder']),
            'storage_info': {'size_in_bytes': entry['size_bytes']}, 'modified_time_in_millisecond': entry['modified_at'],
            'download_url': f"https://download-accl.zoho.in/v1/workdrive/download/{entry['id']}?download=true",
        }} for entry in plan}
        self.download_status = 200
        self.download_content = b'content'
        self.download_requests = []

        def handle(request):
            if request.method == 'POST':
                return httpx.Response(200, json={'access_token': 'test-token', 'api_domain': 'https://www.zohoapis.in'})
            if request.url.host == 'download-accl.zoho.in':
                self.download_requests.append(request)
                return httpx.Response(self.download_status, content=self.download_content, headers={'location': 'https://untrusted.invalid/file'})
            if request.url.path.endswith('/files'):
                parent = request.url.path.split('/')[-2]
                records = [record for record in self.source_records.values() if record['attributes']['parent_id'] == parent]
                return httpx.Response(200, json={'data': [] if request.url.params.get('page[offset]') != '0' else records})
            return httpx.Response(200, json={'data': self.source_records[request.url.path.split('/')[-1]]})

        client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False)
        self.addCleanup(client.close)
        return WorkDriveReader(client, {'ZOHO_CLIENT_ID': 'test', 'ZOHO_CLIENT_SECRET': 'test', 'ZOHO_REFRESH_TOKEN': 'test'})


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.puts = 0
        self.corrupt_read = False

    def head_bucket(self, **options):
        return {}

    def head_object(self, **options):
        content = self.objects.get(options['Key'])
        if content is None:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        return {'ContentLength': len(content), 'ETag': '"' + hashlib.sha256(content).hexdigest() + '"'}

    def put_object(self, **options):
        if options.get('IfNoneMatch') != '*' or options['Key'] in self.objects:
            raise AssertionError('Overwrite attempted')
        content = options['Body'].read()
        if len(content) != options['ContentLength']:
            raise AssertionError('Upload size mismatch')
        self.objects[options['Key']] = content
        self.puts += 1
        return self.head_object(**options)

    def get_object(self, **options):
        if options['IfMatch'] != self.head_object(**options)['ETag']:
            raise AssertionError('Readback not bound to object identity')
        content = self.objects[options['Key']]
        return {'Body': io.BytesIO(b'changed' if self.corrupt_read else content)}

    def close(self):
        pass


class MigrationTests(SourceFixture, unittest.TestCase):
    def inventory(self, plan):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'inventory'
        connection = open_inventory(path)
        initialize_roots(connection, [{'id': GENERAL_ID, 'name': 'General', 'reported_storage': {}, 'partial_access': False}])
        with connection:
            connection.execute('UPDATE entries SET scanned=1 WHERE id=?', (GENERAL_ID,))
            for entry in plan[1:]:
                connection.execute('''INSERT INTO entries(id,root_id,parent_id,name,is_folder,size_bytes,native_kind,modified_at,scanned,observed_at)
                    VALUES (:id,:root_id,:parent_id,:name,:is_folder,:size_bytes,:native_kind,:modified_at,:scanned,'test')''', entry)
        connection.close()
        return path / 'inventory.sqlite3'

    def test_plan_preserves_wrapper_empty_folders_and_names(self):
        plan = load_plan(self.inventory(sample_plan()))
        self.assertEqual(plan_summary(plan)['folders_including_general'], 3)
        self.assertEqual(plan_summary(plan)['files'], 2)
        self.assertEqual(plan_summary(plan)['bytes'], 14)
        self.assertEqual({entry['path'] for entry in plan}, {entry['path'] for entry in sample_plan()})

    def test_plan_rejects_incomplete_unsafe_and_duplicate_entries(self):
        for field, value in [('scanned', 0), ('name', '../unsafe'), ('name', ' General'), ('name', 'Engineering')]:
            with self.subTest(field=field, value=value):
                plan = sample_plan()
                plan[1][field] = value
                with self.assertRaises(InventoryError):
                    load_plan(self.inventory(plan))

    def test_plan_excludes_other_source_root(self):
        plan = sample_plan()
        extra = dict(plan[-1], id='other', root_id='mikan', parent_id='mikan', name='Private.dwg')
        loaded = load_plan(self.inventory([*plan, extra]))
        self.assertNotIn('other', {entry['id'] for entry in loaded})

    def test_source_download_and_reconciliation(self):
        plan = sample_plan()
        reader = self.source_reader(plan)
        verify_source(reader, plan)
        output = io.BytesIO()
        self.assertEqual(download_source(reader, plan[-1], output), hashlib.sha256(b'content').hexdigest())
        self.assertEqual(output.read(), b'content')
        self.assertEqual(self.download_requests[0].url.params['download'], 'true')

    def test_source_changes_stop_before_download(self):
        plan = sample_plan()
        reader = self.source_reader(plan)
        self.source_records['file2']['attributes']['modified_time_in_millisecond'] = '101'
        with self.assertRaises(InventoryError):
            verify_source(reader, plan)
        with self.assertRaises(InventoryError):
            download_source(reader, plan[-1], io.BytesIO())
        self.assertFalse(self.download_requests)

    def test_download_host_redirect_size_and_rate_limit_guards(self):
        plan = sample_plan()
        for mode in ('host', 'redirect', 'short', 'large'):
            with self.subTest(mode=mode):
                reader = self.source_reader(plan)
                if mode == 'host':
                    self.source_records['file2']['attributes']['download_url'] = 'https://untrusted.invalid/file'
                if mode == 'redirect':
                    self.download_status = 302
                if mode == 'short':
                    self.download_content = b'short'
                if mode == 'large':
                    self.download_content = b'excess content'
                with self.assertRaises(InventoryError):
                    download_source(reader, plan[-1], io.BytesIO())
                self.assertEqual(len(self.download_requests), 0 if mode == 'host' else 1)

        reader = self.source_reader(plan)
        self.download_status = 429
        with self.assertRaises(TransientProviderError):
            download_source(reader, plan[-1], io.BytesIO())
        self.assertEqual(len(self.download_requests), 1)

    def test_destination_and_confirmation_never_fall_back_to_local(self):
        for dsn in ('', 'dbname=mikan', 'host=/tmp dbname=mikan', 'host=localhost dbname=mikan', 'host=127.0.0.1 dbname=mikan'):
            with self.subTest(dsn=dsn), patch.dict(os.environ, {'MIKAN_MIGRATION_DATABASE_URL': dsn}):
                with self.assertRaises(InventoryError):
                    migration_dsn()
        with patch('zoho_migrate.load_plan', return_value=sample_plan()), patch('zoho_migrate.psycopg.connect') as connect:
            self.assertEqual(main(['--execute']), 1)
            connect.assert_not_called()


@unittest.skipUnless(os.environ.get('MIKAN_TREE_TEST_SOCKET'), 'Explicit scratch PostgreSQL socket required')
class MigrationDatabaseTests(SourceFixture, unittest.TestCase):
    def setUp(self):
        test_company_root.CompanyRootDatabaseTests.setUp(self)
        self.connection.execute('UPDATE company SET name=%s WHERE id=%s', (COMPANY_NAME, self.company))
        self.connection.execute('INSERT INTO company_upload_policy(company_id,connection_id,enabled) VALUES (%s,%s,true)', (self.company, self.storage))
        self.plan = sample_plan()
        self.reader = self.source_reader(self.plan)
        self.storage_client = MemoryStorage()

    def execute_migration(self):
        with patch('uploads.storage_client', return_value=self.storage_client):
            migrate(self.connection, self.reader, self.plan)

    def test_full_transfer_and_rerun_preserve_ids_bytes_and_team_quota(self):
        self.execute_migration()
        first = destination_context(self.connection, self.plan)[2]
        self.assertEqual(len(first), 5)
        self.assertTrue(all(record['state'] == 'ready' for record in first.values()))
        self.assertEqual(sum(record['size_bytes'] for record in first.values()), 14)
        self.assertEqual(first['file1']['sha256'], hashlib.sha256(b'content').hexdigest())
        self.execute_migration()
        second = destination_context(self.connection, self.plan)[2]
        self.assertEqual({key: value['id'] for key, value in first.items()}, {key: value['id'] for key, value in second.items()})
        self.assertEqual(self.storage_client.puts, 2)
        team = self.connection.execute('SELECT storage_quota_bytes,storage_used_bytes FROM team WHERE id=%s', (self.team,)).fetchone()
        self.assertEqual(team, {'storage_quota_bytes': 5000000000000, 'storage_used_bytes': 0})
        self.assertEqual(self.connection.execute('SELECT count(*) AS total FROM stored_file WHERE company_id=%s', (self.company,)).fetchone()['total'], 0)

    def test_failed_readback_stays_pending_and_resume_never_overwrites(self):
        self.storage_client.corrupt_read = True
        with self.assertRaises(InventoryError):
            self.execute_migration()
        pending = destination_context(self.connection, self.plan)[2]
        self.assertEqual(pending['file1']['state'], 'pending')
        self.assertEqual(self.storage_client.puts, 1)
        self.storage_client.corrupt_read = False
        self.execute_migration()
        completed = destination_context(self.connection, self.plan)[2]
        self.assertEqual(pending['file1']['id'], completed['file1']['id'])
        self.assertEqual(completed['file1']['state'], 'ready')
        self.assertEqual(self.storage_client.puts, 2)

    def test_existing_root_blocks_without_partial_reservations(self):
        self.connection.execute("INSERT INTO company_root_entry(company_id,kind,parent,name) VALUES (%s,'folder','','General')", (self.company,))
        with self.assertRaises(InventoryError):
            reserve_destination(self.connection, self.plan)
        self.assertEqual(self.connection.execute('SELECT count(*) AS total FROM company_root_entry WHERE company_id=%s', (self.company,)).fetchone()['total'], 1)

    def test_legacy_general_and_insufficient_capacity_block(self):
        with self.connection.transaction(force_rollback=True):
            self.connection.execute("INSERT INTO data_folder(company_id,team_id,owner_id,path) VALUES (%s,%s,%s,'General/Existing')", (self.company, self.team, self.owner))
            with self.assertRaises(InventoryError):
                reserve_destination(self.connection, self.plan)
        self.connection.execute('UPDATE team SET storage_quota_bytes=16000000000000 WHERE id=%s', (self.team,))
        with self.assertRaises(InventoryError):
            reserve_destination(self.connection, self.plan)
        self.assertEqual(self.connection.execute('SELECT count(*) AS total FROM company_root_entry WHERE company_id=%s', (self.company,)).fetchone()['total'], 0)

    def test_source_change_and_disabled_storage_block_resume(self):
        reserve_destination(self.connection, self.plan)
        changed = sample_plan()
        changed[-1]['size_bytes'] = 8
        with self.assertRaises(InventoryError):
            reserve_destination(self.connection, changed)
        self.connection.execute('UPDATE storage_connection SET enabled=false WHERE id=%s', (self.storage,))
        with self.assertRaises(InventoryError):
            reserve_destination(self.connection, self.plan)
        self.assertEqual(self.storage_client.puts, 0)


if __name__ == '__main__':
    unittest.main()