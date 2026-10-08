import os
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from company_root import RootFolderInput, create_company_root_router, create_root_folder, root_path
from database import get_db


class CompanyRootTests(unittest.TestCase):
    def test_general_is_company_root_folder(self):
        folder = RootFolderInput(name='General')
        self.assertEqual(root_path(folder.parent, folder.name), 'General')
        self.assertEqual(folder.model_dump(), {'name': 'General', 'parent': ''})

    def test_hierarchy_and_spaces_preserved(self):
        folder = RootFolderInput(name='New folder', parent='General/Driver')
        self.assertEqual(root_path(folder.parent, folder.name), 'General/Driver/New folder')

    def test_rejects_drive_assignment_and_unsafe_paths(self):
        for extra in ({'team_id': 1}, {'owner_id': 1}):
            with self.assertRaises(ValidationError):
                RootFolderInput(name='General', **extra)
        for name in ('', '..', 'General/Driver'):
            with self.assertRaises(ValidationError):
                RootFolderInput(name=name)
        for parent in ('../General', '/General', 'General//Driver'):
            with self.assertRaises(ValidationError):
                RootFolderInput(name='Driver', parent=parent)
        with self.assertRaises(ValueError):
            root_path('a' * 250, 'Driver')

    def test_existing_folder_is_not_silently_merged(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = {'kind': 'folder', 'source_id': 'different'}
        with self.assertRaises(HTTPException) as caught:
            create_root_folder(connection, 7, RootFolderInput(name='General'), 'zohoGeneral')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertFalse(any('INSERT' in call.args[0] for call in connection.execute.call_args_list))

    def test_root_routes_registered(self):
        from main import app
        paths = app.openapi()['paths']
        self.assertIn('/company/teams/data/root', paths)
        self.assertIn('/company/teams/data/root/files/{identifier}/content', paths)
        self.assertNotIn('/company/teams/data/root/access', paths)

    def test_api_company_scope_and_origin(self):
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = None
        app = FastAPI()

        def origin():
            raise HTTPException(403, 'Blocked origin')

        app.include_router(create_company_root_router(lambda: {'company_id': 7, 'name': 'Admin'}, origin))
        app.dependency_overrides[get_db] = lambda: connection
        with TestClient(app) as client, patch('company_root.list_rows', return_value={'items': [], 'total': 0}) as listing:
            self.assertEqual(client.get('/company/teams/data/root?company_id=99').status_code, 200)
            for table in ('stored_file', 'data_folder', 'company_root_entry'):
                self.assertIn(table, listing.call_args.args[1])
            self.assertEqual(listing.call_args.args[4][:3], [7, '', ''])
            self.assertEqual(client.post('/company/teams/data/root/folders', json={'name': 'General'}).status_code, 403)
            with patch('company_root.stream_file') as stream:
                response = client.get('/company/teams/data/root/files/00000000-0000-0000-0000-000000000001/content')
                self.assertEqual(response.status_code, 404)
                self.assertEqual(connection.execute.call_args.args[1][1], 7)
                stream.assert_not_called()


@unittest.skipUnless(os.environ.get('MIKAN_TREE_TEST_SOCKET'), 'Explicit scratch PostgreSQL socket required')
class CompanyRootDatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        socket = Path(os.environ['MIKAN_TREE_TEST_SOCKET']).resolve()
        if socket.parent != Path('/tmp').resolve() or not socket.name.startswith('mikan-tree.'):
            raise RuntimeError('Refusing non-scratch database')
        self.connection = psycopg.connect(host=str(socket), dbname='mikan_root_visibility', row_factory=dict_row)
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.transaction(force_rollback=True)
        self.transaction.__enter__()
        self.addCleanup(self.transaction.__exit__, None, None, None)
        self.company = self.connection.execute("INSERT INTO company(name,mobile,email,address) VALUES ('Tree test','1234567890','tree@example.invalid','Test') RETURNING id").fetchone()['id']
        self.team = self.connection.execute("INSERT INTO team(company_id,name,storage_quota_bytes) VALUES (%s,'Existing team',5000000000000) RETURNING id", (self.company,)).fetchone()['id']
        self.owner = self.connection.execute("INSERT INTO team_account(company_id,team_id,name,email,mobile,role,status,auth_type) VALUES (%s,%s,'Member',%s,'1234567890','member','active','otp') RETURNING id", (self.company, self.team, f'{uuid4()}@example.invalid')).fetchone()['id']
        self.storage = self.connection.execute("INSERT INTO storage_connection(provider,name,bucket,region,endpoint,access_key_encrypted,secret_key_encrypted,enabled) VALUES ('s3',%s,%s,'ap-south-1','https://s3.ap-south-1.amazonaws.com','fake','fake',true) RETURNING id", (str(uuid4()), str(uuid4()))).fetchone()['id']
        app = FastAPI()
        app.include_router(create_company_root_router(lambda: {'company_id': self.company, 'name': 'Admin'}, lambda: None))
        app.dependency_overrides[get_db] = lambda: self.connection
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def file(self, name, folder='', state='ready'):
        return self.connection.execute("""INSERT INTO stored_file(company_id,team_id,owner_id,connection_id,object_key,etag,name,folder,size_bytes,state)
            VALUES (%s,%s,%s,%s,%s,'fake',%s,%s,12,%s) RETURNING *""",
            (self.company, self.team, self.owner, self.storage, str(uuid4()), name, folder, state)).fetchone()

    def browse(self, folder='', **params):
        response = self.client.get('/company/teams/data/root', params={'folder': folder, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_existing_and_future_team_content_visible_without_quota_change(self):
        record = self.file('drawing.pdf', 'Engineering/Plans')
        self.connection.execute('INSERT INTO data_folder(company_id,team_id,owner_id,path) VALUES (%s,%s,%s,%s)',
            (self.company, self.team, self.owner, 'Engineering/Empty'))
        before = self.connection.execute('SELECT storage_quota_bytes,storage_used_bytes FROM team WHERE id=%s', (self.team,)).fetchone()
        self.assertEqual([item['path'] for item in self.browse()['items']], ['Engineering'])
        self.assertEqual({item['path'] for item in self.browse('Engineering')['items']}, {'Engineering/Empty', 'Engineering/Plans'})
        self.assertEqual(self.browse('Engineering/Empty')['total'], 0)
        self.assertEqual(self.browse('Engineering/Plans')['items'][0]['id'], str(record['id']))
        after = self.connection.execute('SELECT storage_quota_bytes,storage_used_bytes FROM team WHERE id=%s', (self.team,)).fetchone()
        self.assertEqual(before, after)
        self.assertEqual(after['storage_quota_bytes'], 5000000000000)
        future = self.file('future.pdf', 'Engineering/Plans')
        self.assertIn(str(future['id']), [item['id'] for item in self.browse('Engineering/Plans')['items']])
        self.assertEqual(self.connection.execute('SELECT count(*) AS total FROM stored_file WHERE company_id=%s', (self.company,)).fetchone()['total'], 2)
        preserved = self.connection.execute('SELECT owner_id,team_id,object_key FROM stored_file WHERE id=%s', (record['id'],)).fetchone()
        self.assertEqual(preserved, {key: record[key] for key in ('owner_id', 'team_id', 'object_key')})

    def test_company_root_creation_and_legacy_parent(self):
        self.file('drawing.pdf', 'Engineering/Plans')
        response = self.client.post('/company/teams/data/root/folders', json={'name': 'General'})
        self.assertEqual(response.status_code, 201, response.text)
        response = self.client.post('/company/teams/data/root/folders', json={'name': 'New folder', 'parent': 'Engineering'})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn('Engineering/New folder', [item['path'] for item in self.browse('Engineering')['items']])
        for name, parent in [('Engineering', ''), ('drawing.pdf', 'Engineering/Plans')]:
            response = self.client.post('/company/teams/data/root/folders', json={'name': name, 'parent': parent})
            self.assertEqual(response.status_code, 409, response.text)
        self.connection.execute("INSERT INTO company_root_entry(company_id,kind,parent,name) VALUES (%s,'folder','','Engineering')", (self.company,))
        self.assertEqual([item['path'] for item in self.browse()['items']].count('Engineering'), 1)

    def test_company_isolation_downloads_and_hidden_states(self):
        record = self.file('drawing.pdf', 'Engineering/Plans')
        for state in ('trashed', 'purging', 'purged', 'cancelled'):
            self.file(state + '.pdf', 'Engineering/Plans', state)
        self.assertEqual(self.browse('Engineering/Plans')['total'], 1)
        other = self.connection.execute("INSERT INTO company(name,mobile,email,address) VALUES ('Other','1234567890','other@example.invalid','Test') RETURNING id").fetchone()['id']
        self.connection.execute("INSERT INTO company_root_entry(company_id,kind,parent,name) VALUES (%s,'folder','','Other private')", (other,))
        self.assertEqual([item['path'] for item in self.browse(company_id=other)['items']], ['Engineering'])
        self.assertEqual(self.client.get('/company/teams/data/root', params={'folder': 'Other private'}).status_code, 404)
        with patch('company_root.stream_file', return_value=Response('content')) as stream:
            response = self.client.get(f"/company/teams/data/root/files/{record['id']}/content")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(stream.call_args.args[0]['object_key'], record['object_key'])
        with patch('company_root.stream_file') as stream:
            self.assertEqual(self.client.get(f'/company/teams/data/root/files/{uuid4()}/content').status_code, 404)
            stream.assert_not_called()

    def test_root_import_records_search_pagination_and_dates(self):
        create_root_folder(self.connection, self.company, RootFolderInput(name='General'))
        imported = self.connection.execute("""INSERT INTO company_root_entry(company_id,kind,parent,name,source_id,size_bytes,
            connection_id,object_key,etag,sha256,uploaded_at) VALUES (%s,'file','General','import.pdf',%s,24,%s,%s,'fake',%s,clock_timestamp()) RETURNING *""",
            (self.company, str(uuid4()), self.storage, str(uuid4()), 'a' * 64)).fetchone()
        self.assertEqual(self.browse('General')['items'][0]['id'], str(imported['id']))
        with patch('company_root.stream_file', return_value=Response('content')) as stream:
            self.assertEqual(self.client.get(f"/company/teams/data/root/files/{imported['id']}/content").status_code, 200)
            self.assertEqual(stream.call_args.args[0]['object_key'], imported['object_key'])
        for number in range(12):
            self.file(f'plan-{number:02}.pdf', 'General')
        first = self.browse('General', search='plan-')
        second = self.browse('General', search='plan-', page=2)
        self.assertEqual(first['total'], 12)
        self.assertEqual(len(first['items']), 10)
        self.assertEqual(len(second['items']), 2)
        self.assertFalse({item['id'] for item in first['items']} & {item['id'] for item in second['items']})
        self.assertEqual(self.browse('General', to_date='2000-01-01')['total'], 0)


if __name__ == '__main__':
    unittest.main()