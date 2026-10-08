import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
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
            self.assertEqual(listing.call_args.args[4][:3], [7, '', ''])
            self.assertEqual(client.post('/company/teams/data/root/folders', json={'name': 'General'}).status_code, 403)
            with patch('company_root.stream_file') as stream:
                response = client.get('/company/teams/data/root/files/00000000-0000-0000-0000-000000000001/content')
                self.assertEqual(response.status_code, 404)
                self.assertEqual(connection.execute.call_args.args[1][1], 7)
                stream.assert_not_called()


if __name__ == '__main__':
    unittest.main()