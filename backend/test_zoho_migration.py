import hashlib
import unittest
from io import BytesIO
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from database import get_db
from zoho_inventory import InventoryError
from zoho_migration import MIKAN_ROOT_ID, MigrationDeferred, create_migration_router, current_entry, download_historical, exact_source_size, execute_job, historical_url, historical_versions, inventory_folder, process_migrations, prune_unstarted_current_version_duplicates, reader_relationship, repair_legacy_destination, reserve_items, source_folder, transfer_item, try_lock_root, verify_ready_checkpoint


class Transaction:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ZohoMigrationTests(unittest.TestCase):
    def test_company_root_lock_contention_defers_without_waiting(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {'acquired': False}

        with self.assertRaises(MigrationDeferred):
            try_lock_root(connection, 7)

        query, values = connection.execute.call_args.args
        self.assertIn('pg_try_advisory_xact_lock', query)
        self.assertEqual(values, ('company-root:7',))

    def test_source_folder_preserves_identity_name_and_bytes(self):
        folder = source_folder({'id': 'folder123', 'attributes': {
            'name': '6. Lesson Learned', 'is_folder': True, 'storage_info': {'size_in_bytes': '421752'},
        }})
        self.assertEqual(folder, {'source_folder_id': 'folder123', 'name': '6. Lesson Learned', 'size_bytes': 421752})

    @patch('zoho_migration.children')
    def test_inventory_uses_folder_listing_metadata_without_direct_lookup(self, children):
        root = {'id': 'project123', 'attributes': {'name': 'Project-2020', 'is_folder': True}}
        children.side_effect = [[root], []]
        reader = Mock()

        items = inventory_folder(reader, 'project123', 'Project-2020')

        self.assertEqual([(item['kind'], item['source_id']) for item in items], [('folder', 'project123')])
        reader.get.assert_not_called()

    @patch('zoho_migration.children', return_value=[])
    def test_inventory_rejects_root_missing_from_mikan_listing(self, _children):
        with self.assertRaisesRegex(InventoryError, 'no longer uniquely present under Mikan'):
            inventory_folder(Mock(), 'project123', 'Project-2020')

    def test_normalized_current_entry_fields_override_raw_metadata(self):
        entry = current_entry({
            'source_id': 'safe123', 'source_name': 'Safe name.pdf', 'source_parent_id': 'parent123',
            'size_bytes': 42, 'source_modified_at': '1700000000', 'destination_path': 'Folder/Safe name.pdf',
            'source_metadata': {'id': 'wrong', 'name': 'wrong.pdf', 'size_bytes': 9, 'path': 'wrong'},
        })
        self.assertEqual((entry['id'], entry['name'], entry['size_bytes'], entry['path']),
                         ('safe123', 'Safe name.pdf', 42, 'Folder/Safe name.pdf'))

    def test_relationship_refreshes_once_after_unauthorized_token(self):
        reader = Mock(access_token='expired', api_domain='https://www.zohoapis.in', expires_at=float('inf'), requests=0)
        unauthorized = Mock(status_code=401)
        successful = Mock(status_code=200)
        successful.json.return_value = {'data': [{'id': 'version-1'}]}
        reader.client.get.side_effect = [unauthorized, successful]
        reader.refresh.side_effect = lambda: setattr(reader, 'access_token', 'fresh')
        records = reader_relationship(reader, 'file123', 'versions')
        self.assertEqual(records, [{'id': 'version-1'}])
        self.assertEqual(reader.refresh.call_count, 1)
        self.assertEqual(reader.requests, 2)
        self.assertEqual(reader.client.get.call_args_list[1].kwargs['headers']['Authorization'], 'Zoho-oauthtoken fresh')

    def test_relationship_reports_http_400_with_file_context(self):
        reader = Mock(access_token='token', api_domain='https://www.zohoapis.in', expires_at=float('inf'), requests=0)
        reader.client.get.return_value = Mock(status_code=400)

        with self.assertRaisesRegex(InventoryError, 'approvedversions metadata for file file123 returned HTTP 400'):
            reader_relationship(reader, 'file123', 'approvedversions')

    def test_relationship_distinguishes_invalid_json_and_structured_error(self):
        reader = Mock(access_token='token', api_domain='https://www.zohoapis.in', expires_at=float('inf'), requests=0)
        invalid = Mock(status_code=200)
        invalid.json.side_effect = ValueError
        structured = Mock(status_code=200)
        structured.json.return_value = {'errors': [{'id': 'hidden'}]}
        reader.client.get.side_effect = [invalid, structured]

        with self.assertRaisesRegex(InventoryError, 'invalid JSON'):
            reader_relationship(reader, 'file123', 'versions')
        with self.assertRaisesRegex(InventoryError, 'structured API error'):
            reader_relationship(reader, 'file123', 'versions')

    def test_exact_source_size_accepts_known_exact_numeric_fields(self):
        self.assertEqual(exact_source_size({'size_in_bytes': '42'}), 42)
        self.assertEqual(exact_source_size({'file_size': 42.0}), 42)
        with self.assertRaises(InventoryError):
            exact_source_size({'size': '42 KB'})

    def test_historical_url_falls_back_to_version_download_for_unpreviewable_file(self):
        reader = Mock()
        reader.version_preview_info.return_value = {'data': {'attributes': {
            'preview_status': -14, 'size': '12.0 KB', 'size_in_bytes': '12285',
        }}}
        item = {
            'source_file_id': 'whiteboard123', 'source_version_id': 'whiteboard123-456',
            'version_label': '1.0', 'size_bytes': 12285,
        }

        url = historical_url(reader, item)

        self.assertEqual(url.host, 'download-accl.zoho.in')
        self.assertEqual(url.path, '/v1/workdrive/download/whiteboard123')
        self.assertEqual(url.params['version'], '1.0')

    def test_historical_url_uses_canonical_download_for_variable_preview_url(self):
        reader = Mock()
        reader.version_preview_info.return_value = {'data': {'attributes': {
            'preview_data_url': 'https://untrusted.invalid/unstructured?version=999',
            'size': 42,
        }}}
        item = {
            'source_file_id': 'file123', 'source_version_id': 'file123-456',
            'version_label': '1.0', 'size_bytes': 42,
        }

        url = historical_url(reader, item)

        self.assertEqual(url.host, 'download-accl.zoho.in')
        self.assertEqual(url.path, '/v1/workdrive/download/file123')
        self.assertEqual(url.params['version'], '1.0')
        reader.version_preview_info.assert_not_called()

    def test_historical_url_does_not_send_provider_preview_query(self):
        reader = Mock()
        reader.version_preview_info.return_value = {'data': {'attributes': {
            'preview_data_url': 'https://download-accl.zoho.in/v1/workdrive/previewdata/file123?version=456&token=secret',
            'size': 42,
        }}}
        item = {
            'source_file_id': 'file123', 'source_version_id': 'file123-456',
            'version_label': '1.0', 'size_bytes': 42,
        }

        url = historical_url(reader, item)

        self.assertEqual(list(url.params.multi_items()), [('version', '1.0')])
        reader.version_preview_info.assert_not_called()

    def test_historical_url_rejects_invalid_identity_before_credentials(self):
        item = {
            'source_file_id': '../file123', 'source_version_id': 'file123-456',
            'version_label': '1.0', 'size_bytes': 42,
        }

        with self.assertRaisesRegex(InventoryError, 'credentials were not sent'):
            historical_url(Mock(), item)

    def test_historical_download_refreshes_once_after_unauthorized_token(self):
        reader = Mock(access_token='expired', expires_at=float('inf'))
        unauthorized = Mock(status_code=401, headers={})
        unauthorized.__enter__ = Mock(return_value=unauthorized)
        unauthorized.__exit__ = Mock(return_value=False)
        successful = Mock(status_code=200, headers={})
        successful.iter_bytes.return_value = [b'fresh bytes']
        successful.__enter__ = Mock(return_value=successful)
        successful.__exit__ = Mock(return_value=False)
        reader.client.stream.side_effect = [unauthorized, successful]
        reader.refresh.side_effect = lambda: setattr(reader, 'access_token', 'fresh')
        output = BytesIO(b'stale bytes')
        item = {
            'source_file_id': 'file123', 'source_version_id': 'file123-456',
            'version_label': '1.0', 'size_bytes': 11,
        }

        digest = download_historical(reader, item, output)

        self.assertEqual(digest, hashlib.sha256(b'fresh bytes').hexdigest())
        self.assertEqual(output.read(), b'fresh bytes')
        self.assertEqual(reader.refresh.call_count, 1)
        self.assertEqual(reader.client.stream.call_args_list[1].kwargs['headers']['Authorization'],
                         'Zoho-oauthtoken fresh')

    def test_historical_download_refreshes_expired_token_before_streaming(self):
        reader = Mock(access_token='expired', expires_at=0)
        successful = Mock(status_code=200, headers={})
        successful.iter_bytes.return_value = [b'fresh bytes']
        successful.__enter__ = Mock(return_value=successful)
        successful.__exit__ = Mock(return_value=False)
        reader.client.stream.return_value = successful
        reader.refresh.side_effect = lambda: setattr(reader, 'access_token', 'fresh')
        item = {
            'source_file_id': 'file123', 'source_version_id': 'file123-456',
            'version_label': '1.0', 'size_bytes': 11,
        }

        download_historical(reader, item, BytesIO())

        reader.refresh.assert_called_once_with()
        self.assertEqual(reader.client.stream.call_args.kwargs['headers']['Authorization'],
                         'Zoho-oauthtoken fresh')

    def test_historical_download_stops_after_second_unauthorized_response(self):
        reader = Mock(access_token='expired', expires_at=float('inf'))
        responses = []
        for _attempt in range(2):
            response = Mock(status_code=401, headers={})
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            responses.append(response)
        reader.client.stream.side_effect = responses
        reader.refresh.side_effect = lambda: setattr(reader, 'access_token', 'fresh')
        item = {
            'source_file_id': 'file123', 'source_version_id': 'file123-456',
            'version_label': '1.0', 'size_bytes': 11,
        }

        with self.assertRaisesRegex(InventoryError, 'HTTP 401'):
            download_historical(reader, item, BytesIO())

        self.assertEqual(reader.refresh.call_count, 1)
        self.assertEqual(reader.client.stream.call_count, 2)

    def test_version_inventory_excludes_current_and_preserves_older_and_approved(self):
        file_item = {'source_id': 'file123', 'source_parent_id': 'folder123', 'source_name': 'Plan.pdf',
                     'destination_path': 'Mikan/Plans/Plan.pdf', 'size_bytes': 77_479}
        record = lambda identifier, label, size: {'id': identifier, 'attributes': {
            'version_number': label, 'file_size': size,
        }}

        historical, current_id = historical_versions(
            [record('file123-2', 2.0, 77_479), record('file123-1', 1.0, 38_155)],
            [record('file123-approved', 0.5, 12_000)], file_item,
        )

        self.assertEqual(current_id, 'file123-2')
        self.assertEqual({item['source_id'] for item in historical}, {'file123-1', 'file123-approved'})

    def test_version_inventory_deduplicates_approved_union_by_source_id(self):
        file_item = {'source_id': 'file123', 'source_parent_id': 'folder123', 'source_name': 'Plan.pdf',
                     'destination_path': 'Mikan/Plans/Plan.pdf', 'size_bytes': 77_479}
        record = lambda identifier, label, size: {'id': identifier, 'attributes': {
            'version_number': label, 'file_size': size,
        }}

        historical, _current_id = historical_versions(
            [record('file123-2', 2.0, 77_479), record('file123-1', 1.0, 38_155)],
            [record('file123-1', 1.0, 38_155)], file_item,
        )

        self.assertEqual([item['source_id'] for item in historical], ['file123-1'])

    def test_version_inventory_refuses_ambiguous_highest_active_version(self):
        file_item = {'source_id': 'file123', 'source_parent_id': 'folder123', 'source_name': 'Plan.pdf',
                     'destination_path': 'Mikan/Plans/Plan.pdf', 'size_bytes': 77_479}
        with self.assertRaises(InventoryError):
            historical_versions([
                {'id': 'file123-2a', 'attributes': {'version_number': 2.0, 'file_size': 77_479}},
                {'id': 'file123-2b', 'attributes': {'version_number': 2.0, 'file_size': 77_479}},
            ], [], file_item)

    def test_version_inventory_refuses_current_size_mismatch(self):
        file_item = {'source_id': 'file123', 'source_parent_id': 'folder123', 'source_name': 'Plan.pdf',
                     'destination_path': 'Mikan/Plans/Plan.pdf', 'size_bytes': 77_479}
        with self.assertRaises(InventoryError):
            historical_versions([{'id': 'file123-2', 'attributes': {
                'version_number': 2.0, 'file_size': 1,
            }}], [], file_item)

    def test_version_inventory_accepts_alternate_exact_size_field(self):
        file_item = {'source_id': 'file123', 'source_parent_id': 'folder123', 'source_name': 'Plan.pdf',
                     'destination_path': 'Mikan/Plans/Plan.pdf', 'size_bytes': 77_479}

        historical, current_id = historical_versions([
            {'id': 'file123-2', 'attributes': {'version_number': '2.0', 'size_in_bytes': '77479.0'}},
            {'id': 'file123-1', 'attributes': {'version_number': '1.0', 'storage_info': {'size_in_bytes': 12}}},
        ], [], file_item)

        self.assertEqual(current_id, 'file123-2')
        self.assertEqual(historical[0]['size_bytes'], 12)

    def test_version_inventory_reports_missing_active_versions(self):
        file_item = {'source_id': 'file123', 'source_parent_id': 'folder123', 'source_name': 'Plan.pdf',
                     'destination_path': 'Mikan/Plans/Plan.pdf', 'size_bytes': 77_479}

        with self.assertRaisesRegex(InventoryError, 'no active version metadata'):
            historical_versions([], [], file_item)

    @patch('zoho_migration.object_head')
    def test_ready_checkpoint_uses_object_identity_without_redownloading_bytes(self, object_head):
        object_head.return_value = {'ContentLength': 42, 'ETag': 'etag-1', 'VersionId': 'version-1'}
        record = {
            'state': 'ready', 'size_bytes': 42, 'sha256': 'a' * 64,
            'etag': 'etag-1', 'object_version': 'version-1',
        }

        head = verify_ready_checkpoint(Mock(), {'bucket': 'bucket'}, record, 'a' * 64)

        self.assertEqual(head['ETag'], 'etag-1')
        object_head.assert_called_once()

    @patch('zoho_migration.object_head')
    def test_ready_checkpoint_rejects_changed_destination_identity(self, object_head):
        object_head.return_value = {'ContentLength': 42, 'ETag': 'different', 'VersionId': 'version-1'}
        record = {
            'state': 'ready', 'size_bytes': 42, 'sha256': 'a' * 64,
            'etag': 'etag-1', 'object_version': 'version-1',
        }

        with self.assertRaises(InventoryError):
            verify_ready_checkpoint(Mock(), {'bucket': 'bucket'}, record, 'a' * 64)

    @patch('zoho_migration.download_source')
    @patch('zoho_migration.object_head')
    @patch('zoho_migration.verify_object')
    def test_pending_sha_bound_object_resumes_without_download_or_upload(self, verify_object, object_head, download_source):
        digest = 'a' * 64
        entry_id = uuid4()
        record = {
            'id': entry_id, 'state': 'pending', 'size_bytes': 42, 'sha256': digest,
            'object_key': 'company-root/existing', 'etag': None, 'object_version': None,
        }
        connection = Mock()
        connection.transaction.return_value = Transaction()
        published = {'id': entry_id}
        ready_item = {'id': 1}

        def execute(query, *_args):
            result = Mock()
            if query.startswith('SELECT * FROM company_root_entry'):
                result.fetchone.return_value = record
            elif "SET state='ready',etag=" in query:
                result.fetchone.return_value = published
            elif query.startswith('UPDATE zoho_migration_item'):
                result.fetchone.return_value = ready_item
            return result

        connection.execute.side_effect = execute
        object_head.return_value = {'ContentLength': 42, 'ETag': 'etag-1', 'VersionId': 'version-1'}
        verify_object.return_value = object_head.return_value
        client = Mock()
        job = {'id': uuid4(), 'company_id': 7}
        item = {
            'id': 1, 'kind': 'file', 'destination_entry_id': entry_id,
            'source_file_id': 'file123', 'size_bytes': 42,
        }

        transfer_item(connection, Mock(), client, {'bucket': 'bucket'}, job, item)

        verify_object.assert_called_once_with(client, {'bucket': 'bucket'}, record, digest)
        download_source.assert_not_called()
        client.put_object.assert_not_called()
        publish = next(call for call in connection.execute.call_args_list if "SET state='ready',etag=" in call.args[0])
        self.assertEqual(publish.args[1][:2], ('etag-1', 'version-1'))

    def test_unstarted_duplicate_current_version_checkpoint_is_removed(self):
        version_id = uuid4()
        file_item = {'id': 1, 'kind': 'file', 'source_id': 'file123', 'source_parent_id': 'folder123',
                     'source_name': 'Board.whiteboard', 'destination_path': 'Mikan/Whiteboards/Board.whiteboard',
                     'size_bytes': 42}
        duplicate = {'id': 2, 'kind': 'version', 'source_file_id': 'file123', 'source_version_id': 'file123-1',
                     'version_label': '1.0',
                     'size_bytes': 42, 'destination_version_id': version_id, 'state': 'pending', 'sha256': None}
        connection = Mock()
        connection.transaction.return_value = Transaction()
        connection.execute.return_value.fetchone.return_value = {'id': version_id}
        job = {'id': uuid4(), 'company_id': 7}
        reader = Mock()
        reader.access_token = 'token'
        reader.api_domain = 'https://www.zohoapis.in'
        reader.expires_at = float('inf')
        reader.requests = 0
        active = Mock(status_code=200)
        active.json.return_value = {'data': [{'id': 'file123-1', 'attributes': {
            'version_number': 1.0, 'file_size': 42,
        }}]}
        approved = Mock(status_code=200)
        approved.json.return_value = {'data': []}
        reader.client.get.side_effect = [active, approved]

        retained = prune_unstarted_current_version_duplicates(connection, reader, job, [file_item, duplicate])

        self.assertEqual(retained, [file_item])
        self.assertTrue(any('DELETE FROM company_root_version' in call.args[0]
                            for call in connection.execute.call_args_list))

    def app(self, connection, origin=lambda: None):
        app = FastAPI()
        app.include_router(create_migration_router(lambda: {'company_id': 7, 'name': 'Admin'}, origin))
        app.dependency_overrides[get_db] = lambda: connection
        return app

    @patch('zoho_migration.list_source_folders', return_value=[
        {'source_folder_id': 'lesson123', 'name': '6. Lesson Learned', 'size_bytes': 421752},
    ])
    def test_list_is_company_scoped_and_combines_job_status(self, _folders):
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = [{
            'id': uuid4(), 'source_folder_id': 'lesson123', 'source_folder_name': '6. Lesson Learned',
            'destination_path': '6. Lesson Learned', 'status': 'transferring', 'phase': 'Current files',
            'folders_total': 2, 'folders_complete': 2, 'files_total': 3, 'files_complete': 1,
            'versions_total': 3, 'versions_complete': 0, 'bytes_total': 421752,
            'bytes_complete': 100, 'attempts': 1, 'last_error': None, 'created_at': None,
            'started_at': None, 'completed_at': None, 'updated_at': None,
        }]
        with TestClient(self.app(connection)) as client:
            response = client.get('/company/teams/data/migration?company_id=99')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['source_unavailable'])
        self.assertEqual(response.json()['items'][0]['job']['status'], 'transferring')
        self.assertEqual(connection.execute.call_args.args[1], (7, MIKAN_ROOT_ID))

    @patch('zoho_migration.list_source_folders', side_effect=InventoryError('provider unavailable'))
    def test_list_preserves_durable_job_status_when_zoho_listing_fails(self, _folders):
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = [{
            'id': uuid4(), 'source_folder_id': 'whiteboards123', 'source_folder_name': 'Whiteboards',
            'destination_path': 'Mikan/Whiteboards', 'status': 'failed', 'phase': 'Stopped',
            'folders_total': 1, 'folders_complete': 1, 'files_total': 1, 'files_complete': 1,
            'versions_total': 1, 'versions_complete': 0, 'bytes_total': 24570,
            'bytes_complete': 12285, 'attempts': 1, 'last_error': 'Historical download failed.',
            'created_at': None, 'started_at': None, 'completed_at': None, 'updated_at': None,
        }]

        with TestClient(self.app(connection)) as client:
            response = client.get('/company/teams/data/migration')

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['source_unavailable'])
        self.assertEqual(response.json()['items'][0]['job']['status'], 'failed')

    @patch('zoho_migration.list_source_folders', return_value=[
        {'source_folder_id': 'lesson123', 'name': '6. Lesson Learned', 'size_bytes': 421752},
    ])
    def test_start_is_idempotent_and_origin_protected(self, _folders):
        existing = {'id': uuid4(), 'source_folder_id': 'lesson123', 'status': 'queued'}
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = existing
        with TestClient(self.app(connection)) as client:
            first = client.post('/company/teams/data/migration/jobs', json={'source_folder_id': 'lesson123'})
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()['id'], str(existing['id']))
        self.assertFalse(any('INSERT INTO' in call.args[0] for call in connection.execute.call_args_list))

        def blocked():
            raise HTTPException(403, 'Blocked origin')

        with TestClient(self.app(connection, blocked)) as client:
            self.assertEqual(client.post('/company/teams/data/migration/jobs', json={'source_folder_id': 'lesson123'}).status_code, 403)

    @patch('zoho_migration.list_source_folders', return_value=[
        {'source_folder_id': 'whiteboards123', 'name': 'Whiteboards', 'size_bytes': 12285},
    ])
    def test_new_job_targets_mikan_folder(self, _folders):
        created = {'id': uuid4(), 'source_folder_id': 'whiteboards123', 'status': 'queued'}
        connection = Mock()
        connection.transaction.return_value = Transaction()
        results = iter((None, created))
        connection.execute.return_value.fetchone.side_effect = lambda: next(results)

        with TestClient(self.app(connection)) as client:
            response = client.post('/company/teams/data/migration/jobs', json={'source_folder_id': 'whiteboards123'})

        self.assertEqual(response.status_code, 202, response.text)
        insert = next(call for call in connection.execute.call_args_list
                      if 'INSERT INTO zoho_migration_job' in call.args[0])
        self.assertEqual(insert.args[1][-1], 'Mikan/Whiteboards')

    def test_retry_is_tenant_scoped_and_failed_only(self):
        connection = Mock()
        connection.transaction.return_value = Transaction()
        connection.execute.return_value.fetchone.return_value = None
        identifier = uuid4()
        with TestClient(self.app(connection)) as client:
            response = client.post(f'/company/teams/data/migration/jobs/{identifier}/retry', json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(connection.execute.call_args.args[1], (identifier, 7))

    @patch('zoho_migration.list_source_folders')
    def test_active_job_poll_uses_durable_progress_without_zoho_request(self, list_folders):
        for status in ('queued', 'inventory', 'transferring', 'verifying'):
            with self.subTest(status=status):
                job = {
                    'id': uuid4(), 'source_folder_id': 'updates123',
                    'source_folder_name': 'PROJECT UPDATES', 'status': status,
                    'bytes_total': 367800000,
                }
                connection = Mock()
                connection.execute.return_value.fetchall.return_value = [job]

                with TestClient(self.app(connection)) as client:
                    response = client.get('/company/teams/data/migration')

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['items'][0]['job']['status'], status)
                self.assertFalse(response.json()['source_unavailable'])
        list_folders.assert_not_called()

    @patch('zoho_migration.list_source_folders', return_value=[])
    def test_idle_job_poll_still_refreshes_live_source_folders(self, list_folders):
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = [{
            'id': uuid4(), 'source_folder_id': 'updates123',
            'source_folder_name': 'PROJECT UPDATES', 'status': 'failed',
            'bytes_total': 367800000,
        }]

        with TestClient(self.app(connection)) as client:
            response = client.get('/company/teams/data/migration')

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['source_unavailable'])
        list_folders.assert_called_once_with()

    def test_legacy_destination_rebases_records_without_changing_ids(self):
        folder_id = uuid4()
        file_id = uuid4()
        job_id = uuid4()
        items = [
            {'kind': 'folder', 'destination_path': 'Whiteboards', 'destination_entry_id': folder_id},
            {'kind': 'file', 'destination_path': 'Whiteboards/Meeting.whiteboard', 'destination_entry_id': file_id},
            {'kind': 'version', 'destination_path': 'Whiteboards/Meeting.whiteboard', 'destination_entry_id': file_id},
        ]
        job = {'id': job_id, 'company_id': 7, 'source_folder_name': 'Whiteboards',
               'destination_path': 'Whiteboards'}
        connection = Mock()
        connection.transaction.return_value = Transaction()

        def execute(query, *_args):
            result = Mock()
            if 'pg_try_advisory_xact_lock' in query:
                result.fetchone.return_value = {'acquired': True}
            else:
                result.fetchone.return_value = {'id': uuid4()} if 'kind=\'folder\'' in query else None
            return result

        connection.execute.side_effect = execute

        rebased = repair_legacy_destination(connection, job, items)

        self.assertEqual([item['destination_path'] for item in rebased], [
            'Mikan/Whiteboards', 'Mikan/Whiteboards/Meeting.whiteboard',
            'Mikan/Whiteboards/Meeting.whiteboard',
        ])
        updates = [call.args for call in connection.execute.call_args_list
                   if call.args[0].startswith('UPDATE company_root_entry SET parent=')]
        self.assertEqual([args[1][1] for args in updates], [folder_id, file_id])
        self.assertEqual([args[1][0] for args in updates], ['Mikan', 'Mikan/Whiteboards'])

    @patch('zoho_migration.connect')
    def test_worker_reclaims_interrupted_nonterminal_jobs(self, connect):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.execute.return_value.fetchone.return_value = None
        connect.return_value = connection
        process_migrations()
        query = connection.execute.call_args_list[0].args[0]
        self.assertIn("status IN ('queued','inventory','transferring','verifying')", query)
        self.assertNotIn("'failed'", query)

    @patch('zoho_migration.execute_job', side_effect=HTTPException(404, 'Parent folder not found.'))
    @patch('zoho_migration.connect')
    def test_worker_records_api_validation_failure_once(self, connect, _execute_job):
        job = {'id': uuid4(), 'company_id': 7, 'source_folder_id': 'folder1'}
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)

        def execute(query, *_args):
            result = Mock()
            if 'FROM zoho_migration_job' in query:
                result.fetchone.return_value = job
            elif 'pg_try_advisory_lock' in query:
                result.fetchone.return_value = {'acquired': True}
            return result

        connection.execute.side_effect = execute
        connect.return_value = connection

        process_migrations()

        failed = next(call for call in connection.execute.call_args_list
                      if "status='failed'" in call.args[0])
        self.assertEqual(failed.args[1], ('Parent folder not found.', job['id']))

    @patch('zoho_migration.destination_storage')
    @patch('zoho_migration.create_root_folder')
    def test_reservation_creates_missing_mikan_destination_root(self, create_folder, destination_storage):
        connection = Mock()
        connection.transaction.return_value = Transaction()
        storage = {'id': 24, 'max_file_bytes': 5_000_000_000}
        destination_storage.return_value = storage

        def execute(query, *_args):
            result = Mock()
            if 'pg_try_advisory_xact_lock' in query:
                result.fetchone.return_value = {'acquired': True}
            elif 'WITH folder_sources AS' in query:
                result.fetchone.return_value = None
            elif 'coalesce(sum' in query:
                result.fetchone.return_value = {'bytes': 0}
            return result

        connection.execute.side_effect = execute

        reserved = reserve_items(connection, {'id': uuid4(), 'company_id': 7}, [])

        self.assertEqual(reserved, storage)
        create_folder.assert_called_once()
        _connection, company_id, payload, source_id = create_folder.call_args.args
        self.assertEqual(company_id, 7)
        self.assertEqual(payload.name, 'Mikan')
        self.assertEqual(payload.parent, '')
        self.assertEqual(source_id, MIKAN_ROOT_ID)

    @patch('zoho_migration.migration_credentials', return_value={})
    @patch('zoho_migration.WorkDriveReader')
    @patch('zoho_migration.storage_client')
    @patch('zoho_migration.transfer_item')
    @patch('zoho_migration.reserve_items')
    def test_execute_job_reloads_reserved_destination_ids(self, reserve, transfer, storage_client, _reader, _credentials):
        stale = {'id': 1, 'kind': 'file', 'destination_path': 'Whiteboards/board.png',
                 'source_id': 'file1', 'destination_entry_id': None, 'state': 'pending', 'size_bytes': 42}
        refreshed = {**stale, 'destination_entry_id': uuid4()}
        connection = Mock()
        connection.transaction.return_value = Transaction()
        item_reads = iter(([stale], [refreshed], [{**refreshed, 'state': 'ready', 'sha256': 'digest'}]))

        def execute(query, *_args):
            result = Mock()
            if 'pg_try_advisory_xact_lock' in query:
                result.fetchone.return_value = {'acquired': True}
            elif query.startswith('SELECT * FROM zoho_migration_item'):
                result.fetchall.return_value = next(item_reads)
            elif query.startswith('SELECT * FROM company_root_entry'):
                result.fetchone.return_value = {
                    'id': refreshed['destination_entry_id'], 'state': 'ready', 'size_bytes': 42,
                    'sha256': 'digest', 'etag': 'etag-1', 'object_version': None,
                }
            return result

        connection.execute.side_effect = execute
        reserve.return_value = {'bucket': 'bucket'}
        client = Mock()
        storage_client.return_value = client
        job = {'id': uuid4(), 'company_id': 7, 'source_folder_id': 'folder1',
               'source_folder_name': 'Whiteboards', 'destination_path': 'Whiteboards'}

        with patch('zoho_migration.verify_ready_checkpoint'):
            execute_job(connection, job)

        self.assertEqual(transfer.call_args.args[-1]['destination_entry_id'], refreshed['destination_entry_id'])


if __name__ == '__main__':
    unittest.main()