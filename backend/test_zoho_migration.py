import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from database import get_db
from zoho_inventory import InventoryError
from zoho_migration import MIKAN_ROOT_ID, create_migration_router, current_entry, execute_job, historical_url, process_migrations, reader_relationship, source_folder


class Transaction:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ZohoMigrationTests(unittest.TestCase):
    def test_source_folder_preserves_identity_name_and_bytes(self):
        folder = source_folder({'id': 'folder123', 'attributes': {
            'name': '6. Lesson Learned', 'is_folder': True, 'storage_info': {'size_in_bytes': '421752'},
        }})
        self.assertEqual(folder, {'source_folder_id': 'folder123', 'name': '6. Lesson Learned', 'size_bytes': 421752})

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

    def test_historical_url_rejects_different_version_selector(self):
        reader = Mock()
        reader.version_preview_info.return_value = {'data': {'attributes': {
            'preview_data_url': 'https://download-accl.zoho.in/v1/workdrive/previewdata/file123?version=999',
            'size': 42,
        }}}
        item = {
            'source_file_id': 'file123', 'source_version_id': 'file123-456',
            'version_label': '1.0', 'size_bytes': 42,
        }

        with self.assertRaises(InventoryError):
            historical_url(reader, item)

    def test_historical_preview_url_accepts_source_version_identifier(self):
        reader = Mock()
        reader.version_preview_info.return_value = {'data': {'attributes': {
            'preview_data_url': 'https://download-accl.zoho.in/v1/workdrive/previewdata/file123?version=456',
            'size': 42,
        }}}
        item = {
            'source_file_id': 'file123', 'source_version_id': 'file123-456',
            'version_label': '1.0', 'size_bytes': 42,
        }

        self.assertEqual(historical_url(reader, item).params['version'], '456')

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
        self.assertEqual(response.json()['items'][0]['job']['status'], 'transferring')
        self.assertEqual(connection.execute.call_args.args[1], (7, MIKAN_ROOT_ID))

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

    def test_retry_is_tenant_scoped_and_failed_only(self):
        connection = Mock()
        connection.transaction.return_value = Transaction()
        connection.execute.return_value.fetchone.return_value = None
        identifier = uuid4()
        with TestClient(self.app(connection)) as client:
            response = client.post(f'/company/teams/data/migration/jobs/{identifier}/retry', json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(connection.execute.call_args.args[1], (identifier, 7))

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
            if query.startswith('SELECT * FROM zoho_migration_item'):
                result.fetchall.return_value = next(item_reads)
            elif query.startswith('SELECT * FROM company_root_entry'):
                result.fetchone.return_value = {'id': refreshed['destination_entry_id']}
            return result

        connection.execute.side_effect = execute
        reserve.return_value = {'bucket': 'bucket'}
        client = Mock()
        storage_client.return_value = client
        job = {'id': uuid4(), 'company_id': 7, 'source_folder_id': 'folder1',
               'source_folder_name': 'Whiteboards', 'destination_path': 'Whiteboards'}

        with patch('zoho_migration.verify_object'):
            execute_job(connection, job)

        self.assertEqual(transfer.call_args.args[-1]['destination_entry_id'], refreshed['destination_entry_id'])


if __name__ == '__main__':
    unittest.main()