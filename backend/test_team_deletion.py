import unittest
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from psycopg.errors import ForeignKeyViolation

from database import get_db
from teams import create_team_router


class TeamDeletionTests(unittest.TestCase):
    def setUp(self):
        self.connection = Mock()
        self.transaction = Mock()
        self.transaction.__enter__ = Mock(return_value=self.transaction)
        self.transaction.__exit__ = Mock(return_value=False)
        self.connection.transaction.return_value = self.transaction
        self.company_admin = {'company_id': 7, 'name': 'Company admin'}

        def company():
            return self.company_admin

        def origin():
            return None

        self.company_dependency = company
        self.origin_dependency = origin
        self.app = FastAPI()
        self.app.include_router(create_team_router(company, origin, lambda: {'name': 'Super admin'}))
        self.app.dependency_overrides[get_db] = lambda: self.connection
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def test_company_member_delete_scoped_and_audited(self):
        self.connection.execute.return_value.fetchone.return_value = {'id': 11, 'name': 'Member'}
        response = self.client.post('/company/teams/people/11/delete?company_id=99', json={})
        self.assertEqual(response.status_code, 200, response.text)
        deletion, audit = self.connection.execute.call_args_list
        self.assertEqual(deletion.args[1], (11, 7))
        self.assertIn('company_id=%s', deletion.args[0])
        self.assertIn("'member_delete'", audit.args[0])
        self.assertEqual(audit.args[1][:3], (7, 'Company admin', 'Member'))
        self.transaction.__exit__.assert_called_once_with(None, None, None)

    def test_empty_team_delete_scoped_and_audited(self):
        self.connection.execute.return_value.fetchone.return_value = {'id': 12, 'name': 'Empty team'}
        response = self.client.post('/company/teams/12/delete?company_id=99', json={})
        self.assertEqual(response.status_code, 200, response.text)
        lookup, history, deletion, audit = self.connection.execute.call_args_list
        self.assertIn('FOR UPDATE', lookup.args[0])
        for call in (lookup, history, deletion):
            self.assertEqual(call.args[1], (12, 7))
            self.assertIn('company_id=%s', call.args[0])
        self.assertIn("'team_delete'", audit.args[0])
        self.transaction.__exit__.assert_called_once_with(None, None, None)

    def test_missing_or_other_company_records_return_404(self):
        for path in ('people/11', '12'):
            with self.subTest(path=path):
                self.connection.reset_mock()
                self.connection.execute.return_value.fetchone.return_value = None
                response = self.client.post(f'/company/teams/{path}/delete', json={})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(self.connection.execute.call_count, 1)
                self.assertEqual(self.connection.execute.call_args.args[1][-1], 7)

    def test_member_dependencies_block_deletion(self):
        self.connection.execute.side_effect = ForeignKeyViolation('Dependent file')
        response = self.client.post('/company/teams/people/11/delete', json={})
        self.assertEqual(response.status_code, 409)
        self.assertIn('Revoke access', response.json()['detail'])
        self.assertEqual(self.connection.execute.call_count, 1)
        self.assertIs(self.transaction.__exit__.call_args.args[0], ForeignKeyViolation)

    def test_team_dependencies_roll_back_history_removal(self):
        selected = Mock()
        selected.fetchone.return_value = {'id': 12, 'name': 'Team with files'}
        self.connection.execute.side_effect = [selected, Mock(), ForeignKeyViolation('Dependent file')]
        response = self.client.post('/company/teams/12/delete', json={})
        self.assertEqual(response.status_code, 409)
        self.assertIn('stored data will not be deleted', response.json()['detail'])
        self.assertEqual(self.connection.execute.call_count, 3)
        self.assertIs(self.transaction.__exit__.call_args.args[0], ForeignKeyViolation)

    def test_origin_required_for_both_deletions(self):
        def denied():
            raise HTTPException(403, 'Origin rejected')

        self.app.dependency_overrides[self.origin_dependency] = denied
        for path in ('people/11', '12'):
            self.assertEqual(self.client.post(f'/company/teams/{path}/delete', json={}).status_code, 403)
        self.connection.execute.assert_not_called()

    def test_company_admin_required_for_both_deletions(self):
        def denied():
            raise HTTPException(401, 'Sign in required')

        self.app.dependency_overrides[self.company_dependency] = denied
        for path in ('people/11', '12'):
            self.assertEqual(self.client.post(f'/company/teams/{path}/delete', json={}).status_code, 401)
        self.connection.execute.assert_not_called()

    def test_super_admin_uses_selected_company(self):
        for path in ('people/11', '12'):
            with self.subTest(path=path):
                self.connection.reset_mock()
                self.connection.execute.return_value.fetchone.return_value = {'id': 12, 'name': 'Record'}
                response = self.client.post(f'/admin/management/companies/99/teams/{path}/delete', json={})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.connection.execute.call_args_list[0].args[1], (99,))
                self.assertEqual(self.connection.execute.call_args_list[1].args[1][-1], 99)


if __name__ == '__main__':
    unittest.main()