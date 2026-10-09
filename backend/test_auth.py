import asyncio
import base64
import io
import re
import secrets
import unittest
from contextlib import contextmanager
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch

import httpx
from cryptography.fernet import Fernet

from fastapi.testclient import TestClient
from fastapi import HTTPException
from psycopg.errors import UniqueViolation
from PIL import Image

from create_super_admin import create_admin
from database import connect, get_db, transfer_database
from main import app
from security import hash_password, hash_token, verify_password
from workflows import Graph, validate_graph


class AdminAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(24)
        cls.encoded = hash_password(cls.password)

    def setUp(self):
        self.connection = connect()
        self.transaction = self.connection.transaction(force_rollback=True)
        self.transaction.__enter__()
        self.email = f"auth-test-{secrets.token_hex(8)}@example.com"
        self.admin_id = self.connection.execute(
            "INSERT INTO admin (email, name, password_hash) VALUES (%s, 'Auth test', %s) RETURNING id",
            (self.email, self.encoded),
        ).fetchone()["id"]

        def override_db():
            yield self.connection

        @contextmanager
        def override_transfer():
            yield self.connection

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[transfer_database] = lambda: override_transfer
        self.client = TestClient(app, headers={"Origin": "http://127.0.0.1:3000", "X-Upload-Fingerprint": "a" * 64})

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.transaction.__exit__(None, None, None)
        self.connection.close()

    def login(self, password=None, email=None):
        return self.client.post("/auth/admin/login", json={"email": email or self.email, "password": password or self.password})

    def assert_session_lifetime(self, response, cookie_name):
        self.assertIn("Max-Age=2592000", response.headers["set-cookie"])
        table = 'team_session' if cookie_name == 'mikan_team_session' else 'admin_session'
        token = self.client.cookies.get(cookie_name)
        remaining = self.connection.execute(
            f"SELECT EXTRACT(EPOCH FROM expires_at-clock_timestamp()) AS seconds FROM {table} WHERE token_hash=%s",
            (hash_token(token),)).fetchone()['seconds']
        self.assertGreater(remaining, 2592000 - 60)
        self.assertLessEqual(remaining, 2592000)

    def test_whatsapp_settings_and_consent(self):
        path = '/integrations/whatsapp'
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login()
        settings = {'phone_number_id': '123456789', 'business_id': '987654321', 'access_token': 'test-whatsapp-secret'}
        self.assertEqual(self.client.put(path, json=settings, headers={'Origin': 'https://invalid.example'}).status_code, 403)
        with patch('whatsapp.cipher', return_value=Fernet(Fernet.generate_key())):
            response = self.client.put(path, json=settings)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(settings['access_token'], response.text)
            encrypted = self.connection.execute('SELECT token_encrypted FROM whatsapp_integration WHERE id=1').fetchone()['token_encrypted']
            self.assertNotEqual(encrypted, settings['access_token'])
            self.assertEqual(self.client.put(path, json={**settings, 'access_token': None}).status_code, 200)
            self.assertEqual(self.connection.execute('SELECT token_encrypted FROM whatsapp_integration WHERE id=1').fetchone()['token_encrypted'], encrypted)
        self.assertIn('no-store', self.client.get(path).headers['cache-control'])
        consent = {'company_name': 'Consent test', 'recipient': '+1999' + str(secrets.randbelow(10**10)).zfill(10), 'source': 'Signed onboarding form', 'opted_in': True}
        self.assertEqual(self.client.post(path + '/consents', json={**consent, 'opted_in': False}).status_code, 422)
        self.assertEqual(self.client.post(path + '/consents', json={**consent, 'recipient': '9876543210'}).status_code, 422)
        saved = self.client.post(path + '/consents', json=consent)
        self.assertEqual(saved.status_code, 200, saved.text)
        identifier = saved.json()['id']
        self.assertEqual(self.client.post(path + '/consents', json=consent).status_code, 409)
        self.assertEqual(self.client.post(path + '/consents/999999999999999999999999/revoke', json={}).status_code, 422)
        self.assertEqual(self.client.get(path + '/consents', params={'search': consent['recipient'], 'active': True}).json()['total'], 1)
        self.assertEqual(self.client.get(path + '/consents?from_date=2026-10-10&to_date=2026-10-01').status_code, 422)
        self.assertEqual(self.client.post(f'{path}/consents/{identifier}/revoke', json={}).status_code, 200)
        self.assertEqual(self.client.get(path + '/consents', params={'search': consent['recipient'], 'active': True}).json()['total'], 0)
        with patch('whatsapp.send_template') as provider:
            self.assertEqual(self.client.post(path + '/send', json={'consent_id': identifier, 'company_name': 'Test'}).status_code, 409)
            provider.assert_not_called()

    def test_whatsapp_template_send(self):
        self.login()
        path = '/integrations/whatsapp'
        encryption = Fernet(Fernet.generate_key())
        with patch('whatsapp.cipher', return_value=encryption):
            self.client.put(path, json={'phone_number_id': '123456789', 'business_id': '987654321', 'access_token': 'test-whatsapp-secret'})
            consent = self.client.post(path + '/consents', json={'company_name': 'Template test', 'recipient': '+1999' + str(secrets.randbelow(10**10)).zfill(10), 'source': 'Signed form', 'opted_in': True}).json()
            payload = {'consent_id': consent['id'], 'company_name': 'Mikan Engineering'}
            with patch('whatsapp.httpx.Client') as client:
                provider = client.return_value.__enter__.return_value
                provider.post.return_value = httpx.Response(200, json={'messages': [{'id': 'wamid.test'}]}, request=httpx.Request('POST', 'https://graph.facebook.com'))
                self.assertEqual(self.client.post(path + '/send', json=payload, headers={'Origin': 'https://invalid.example'}).status_code, 403)
                response = self.client.post(path + '/send', json=payload)
                self.assertEqual(response.json()['status'], 'accepted', response.text)
                arguments = provider.post.call_args
                self.assertEqual(arguments.args[0], 'https://graph.facebook.com/v25.0/123456789/messages')
                self.assertEqual(arguments.kwargs['json']['to'], consent['recipient'])
                self.assertEqual(arguments.kwargs['json']['template'], {'name': 'onboarding_client', 'language': {'code': 'en'}, 'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': 'Mikan Engineering'}]}]})
                self.assertEqual(arguments.kwargs['headers']['Authorization'], 'Bearer test-whatsapp-secret')
                self.assertEqual(self.client.post(path + '/send', json=payload).status_code, 429)
                provider.post.assert_called_once()
                self.connection.execute('UPDATE whatsapp_consent SET last_attempt_at=NULL WHERE id=%s', (consent['id'],))
                provider.post.side_effect = httpx.ReadTimeout('provider-secret-must-not-leak')
                response = self.client.post(path + '/send', json=payload)
                self.assertEqual(response.json()['status'], 'unknown')
                self.assertNotIn('provider-secret', response.text)
                self.assertEqual(self.connection.execute('SELECT last_status FROM whatsapp_consent WHERE id=%s', (consent['id'],)).fetchone()['last_status'], 'unknown')
                self.connection.execute('UPDATE whatsapp_consent SET last_attempt_at=NULL WHERE id=%s', (consent['id'],))
                provider.post.side_effect = None
                provider.post.return_value = httpx.Response(400, json={'error': {'message': 'provider-secret-must-not-leak'}}, request=httpx.Request('POST', 'https://graph.facebook.com'))
                rejected = self.client.post(path + '/send', json=payload)
                self.assertEqual(rejected.json()['status'], 'failed')
                self.assertNotIn('provider-secret', rejected.text)
                self.assertEqual(provider.post.call_count, 3)

    def test_super_admin_company_admin_crud(self):
        self.login()
        company = self.client.post('/companies/', json={"name": "CRUD fixture", "email": "crud@example.com", "mobile": "9876543210", "address": "Test address"}).json()
        payload = {"name": "CRUD admin", "email": f"crud-{secrets.token_hex(8)}@example.com", "mobile": "9876543210", "company_id": company['id'], "password": self.password}
        created = self.client.post('/company-admins/', json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        identifier = created.json()['id']
        self.client.post('/auth/company/admin/login', json={"email": payload['email'], "password": self.password})
        updated = {**payload, "name": "Edited admin", "password": None}
        path = f'/company-admins/{identifier}'
        self.assertEqual(self.client.put(path, json=updated, headers={"Origin": "https://invalid.example"}).status_code, 403)
        self.assertEqual(self.client.put(f'/company-admins/{self.admin_id}', json=updated).status_code, 404)
        self.assertEqual(self.client.delete(f'/company-admins/{self.admin_id}').status_code, 404)
        response = self.client.put(path, json=updated)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['name'], 'Edited admin')
        self.assertNotIn('password_hash', response.json())
        self.assertEqual(self.client.get('/auth/company/admin/me').status_code, 401)
        self.assertEqual(self.client.put(path, json={**updated, 'company_id': 9223372036854775807}).status_code, 422)
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(self.client.delete(path).status_code, 404)
        self.client.cookies.delete('mikan_admin_session')
        self.assertEqual(self.client.put(path, json=updated).status_code, 401)
        self.assertEqual(self.client.delete(path).status_code, 401)

    def test_super_admin_member_management(self):
        fixture = self.private_file_fixture()
        path = f'/admin/management/companies/{self.company_id}/teams'
        self.client.cookies.delete('mikan_admin_session')
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login()
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get(path + '/people').json()['total'], 1)
        payload = {'name': 'Updated owner', 'email': self.client.get('/auth/team/me').json()['email'], 'mobile': '9876543210', 'role': 'member', 'team_id': self.team_id}
        self.assertEqual(self.client.post(path + f"/people/{fixture['account_id']}/edit", json=payload).status_code, 200)
        self.assertEqual(self.client.post(path + f"/people/{fixture['account_id']}/delete", json={}).status_code, 409)
        unused = self.connection.execute("""INSERT INTO team_account(company_id,team_id,name,email,mobile,role) VALUES (%s,%s,'Unused',%s,'9876543210','member') RETURNING id""", (self.company_id, self.team_id, f'unused-{secrets.token_hex(8)}@example.com')).fetchone()['id']
        self.assertEqual(self.client.post(path + f'/people/{unused}/delete', json={}, headers={'Origin': 'https://invalid.example'}).status_code, 403)
        self.assertEqual(self.client.post(path + f'/people/{unused}/delete', json={}).status_code, 200)
        self.assertEqual(self.client.post(path + f'/people/{unused}/delete', json={}).status_code, 404)
        self.assertEqual(self.client.get('/admin/management/companies/9223372036854775807/teams').status_code, 404)
        self.assertEqual(self.client.get(path + '/people?from_date=2026-02-01&to_date=2026-01-01').status_code, 422)

    @patch('trash.cleanup_upload')
    @patch('trash.storage_client')
    def test_super_admin_clear_data(self, factory, cleanup):
        fixture = self.private_file_fixture()
        self.login()
        company_id = self.company_id
        selection = {'company_id': company_id, 'companies': True, 'admins': True, 'members': True, 'files': True, 'folders': True}
        self.connection.execute("INSERT INTO data_folder(company_id,team_id,owner_id,path) VALUES (%s,%s,%s,'Test folder')", (company_id, self.team_id, fixture['account_id']))
        self.connection.execute("""INSERT INTO file_version(file_id,company_id,team_id,uploader_id,number,base_version,connection_id,object_key,name,size_bytes,quota_bytes,upload_key)
            VALUES (%s,%s,%s,%s,2,1,%s,'cleanup-revision','revision.txt',11,11,%s)""", (fixture['id'], company_id, self.team_id, fixture['account_id'], fixture['storage_id'], uuid4()))
        base = '/admin/management/clear'
        preview = self.client.post(base + '/preview', json=selection)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()['counts']['files'], 1)
        self.assertEqual(preview.json()['blockers'], [])
        payload = {**selection, 'confirmation': 'CLEAR DATA', 'fingerprint': preview.json()['fingerprint']}
        self.assertEqual(self.client.post(base + '/execute', json={**payload, 'confirmation': 'yes'}).status_code, 422)
        self.assertEqual(self.client.post(base + '/execute', json=payload, headers={'Origin': 'https://invalid.example'}).status_code, 403)
        self.assertEqual(self.client.delete(f'/companies/{company_id}').status_code, 409)
        blocked = {**selection, 'files': False}
        blocked_preview = self.client.post(base + '/preview', json=blocked).json()
        self.assertTrue(blocked_preview['blockers'])
        self.assertEqual(self.client.post(base + '/execute', json={**blocked, 'confirmation': 'CLEAR DATA', 'fingerprint': blocked_preview['fingerprint']}).status_code, 409)
        cleanup.assert_not_called()
        self.connection.execute("UPDATE company SET name='Changed after preview',updated_at=clock_timestamp() WHERE id=%s", (company_id,))
        self.assertEqual(self.client.post(base + '/execute', json=payload).status_code, 409)
        payload['fingerprint'] = self.client.post(base + '/preview', json=selection).json()['fingerprint']
        cleanup.side_effect = RuntimeError('mock storage failure')
        self.assertEqual(self.client.post(base + '/execute', json=payload).status_code, 409)
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 18)
        self.assertIsNotNone(self.connection.execute('SELECT id FROM company WHERE id=%s', (company_id,)).fetchone())
        cleanup.side_effect = None
        response = self.client.post(base + '/execute', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['done'])
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 0)
        response = self.client.post(base + '/execute', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['done'])
        self.assertIsNone(self.connection.execute('SELECT id FROM company WHERE id=%s', (company_id,)).fetchone())
        self.assertIsNotNone(self.connection.execute('SELECT id FROM storage_connection WHERE id=%s', (fixture['storage_id'],)).fetchone())
        self.assertEqual(self.client.get('/auth/admin/me').status_code, 200)
        self.client.cookies.delete('mikan_admin_session')
        self.assertEqual(self.client.post(base + '/execute', json=payload).status_code, 401)
        self.assertEqual(self.client.post(base + '/preview', json=selection).status_code, 401)

    def test_super_admin_delete_empty_company(self):
        self.login()
        created = self.client.post('/companies/', json={'name': 'Delete fixture', 'email': 'delete@example.com', 'mobile': '9876543210', 'address': 'Test address'}).json()
        path = f"/companies/{created['id']}"
        self.assertEqual(self.client.delete(path, headers={'Origin': 'https://invalid.example'}).status_code, 403)
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.delete(path).status_code, 404)

    @patch('trash.cleanup_upload')
    @patch('trash.storage_client')
    def test_super_admin_clear_claim_and_selection(self, factory, cleanup):
        from trash import purge_file

        fixture = self.private_file_fixture()
        self.login()
        base = '/admin/management/clear'
        selection = {'company_id': self.company_id, 'files': True}
        preview = self.client.post(base + '/preview', json=selection).json()
        payload = {**selection, 'confirmation': 'CLEAR DATA', 'fingerprint': preview['fingerprint']}

        def changed_claim(connection, identifier, **options):
            connection.execute("UPDATE stored_file SET name='Changed during claim' WHERE id=%s", (identifier,))
            return purge_file(connection, identifier, **options)

        with patch('management.purge_file', side_effect=changed_claim):
            self.assertEqual(self.client.post(base + '/execute', json=payload).status_code, 409)
        cleanup.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(self.connection.execute('SELECT state FROM stored_file WHERE id=%s', (fixture['id'],)).fetchone()['state'], 'ready')
        self.connection.execute("INSERT INTO data_folder(company_id,team_id,owner_id,path) VALUES (%s,%s,%s,'Retained folder')", (self.company_id, self.team_id, fixture['account_id']))
        other = self.client.post('/companies/', json={'name': 'Retained company', 'email': 'retained@example.com', 'mobile': '9876543210', 'address': 'Test address'}).json()
        self.connection.execute("UPDATE stored_file SET state='cancelled' WHERE id=%s", (fixture['id'],))
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 0)
        none = {'company_id': self.company_id}
        nothing = self.client.post(base + '/preview', json=none).json()
        self.assertEqual(self.client.post(base + '/execute', json={**none, 'confirmation': 'CLEAR DATA', 'fingerprint': nothing['fingerprint']}).status_code, 422)
        payload['fingerprint'] = self.client.post(base + '/preview', json=selection).json()['fingerprint']
        self.assertFalse(self.client.post(base + '/execute', json=payload).json()['done'])
        self.assertTrue(self.client.post(base + '/execute', json=payload).json()['done'])
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 0)
        self.assertIsNone(self.connection.execute('SELECT id FROM stored_file WHERE id=%s', (fixture['id'],)).fetchone())
        for table in ('admin', 'team_account', 'data_folder'):
            self.assertIsNotNone(self.connection.execute(f'SELECT id FROM {table} WHERE company_id=%s', (self.company_id,)).fetchone())
        for identifier in (self.company_id, other['id']):
            self.assertIsNotNone(self.connection.execute('SELECT id FROM company WHERE id=%s', (identifier,)).fetchone())

    def test_manager_drive_listing(self):
        fixture = self.private_file_fixture()
        path = f"/team/files?folder=&owner_id={fixture['account_id']}"
        self.assertEqual(self.client.get(path).status_code, 403)
        manager_id = self.connection.execute("""INSERT INTO team_account(company_id,team_id,name,email,mobile,role,status,auth_type)
            VALUES (%s,%s,'Manager',%s,'9876543210','manager','active','otp') RETURNING id""",
            (self.company_id, self.team_id, f"manager-{secrets.token_hex(8)}@example.com")).fetchone()['id']
        token = secrets.token_urlsafe(32)
        self.connection.execute("INSERT INTO team_session VALUES (%s,%s,clock_timestamp()+INTERVAL '1 hour')", (hash_token(token), manager_id))
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.connection.execute('UPDATE team SET manager_can_view_drives=true WHERE id=%s', (self.team_id,))
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['items'][0]['id'], str(fixture['id']))
        self.assertEqual(response.json()['drive_owner']['name'], 'Owner')
        self.assertIsNone(response.json()['policy'])
        self.assertEqual(self.client.get('/team/files?folder=').json()['total'], 0)
        self.assertEqual(self.client.get('/team/files?folder=&owner_id=9223372036854775807').status_code, 404)
        self.connection.execute("INSERT INTO data_folder(company_id,team_id,owner_id,path) VALUES (%s,%s,%s,'Design')", (self.company_id, self.team_id, fixture['account_id']))
        self.assertEqual(self.client.get(path + '&search=Design').json()['items'][0]['kind'], 'folder')
        self.assertEqual(self.client.get(path + '&from_date=2099-01-01').json()['total'], 0)
        self.assertEqual(self.client.get(path + '&from_date=2026-02-01&to_date=2026-01-01').status_code, 422)
        other_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'Other') RETURNING id", (self.company_id,)).fetchone()['id']
        self.connection.execute('UPDATE team_account SET team_id=%s WHERE id=%s', (other_team, fixture['account_id']))
        self.assertEqual(self.client.get(path).status_code, 404)
        self.connection.execute('UPDATE team SET manager_can_view_drives=false WHERE id=%s', (self.team_id,))
        self.assertEqual(self.client.get(path).status_code, 403)
        self.client.cookies.delete('mikan_team_session')
        self.assertEqual(self.client.get(path).status_code, 401)

    def test_manager_dashboard_usage(self):
        fixture = self.private_file_fixture()
        self.assertEqual(self.client.get('/team/dashboard').status_code, 403)
        self.connection.execute("UPDATE team_account SET role='manager' WHERE id=%s", (fixture['account_id'],))
        response = self.client.get('/team/dashboard?company_id=999&team_id=999')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('private, no-store', response.headers['cache-control'])
        initial = response.json()
        self.assertEqual((initial['used_bytes'], initial['members'], initial['files'], initial['pending_reviews']), (7, 1, 1, 0))
        self.assertEqual(initial['remaining_bytes'], 1000000000 - 7)
        self.assertEqual(initial['recent_files'], [])
        self.assertEqual(initial['largest_files'], [])
        self.connection.execute("""INSERT INTO file_version(file_id,company_id,team_id,uploader_id,number,base_version,
            connection_id,object_key,name,size_bytes,quota_bytes,upload_key)
            VALUES (%s,%s,%s,%s,2,1,%s,'manager-revision','revision.txt',11,11,%s)""",
            (fixture['id'], self.company_id, self.team_id, fixture['account_id'], fixture['storage_id'], uuid4()))
        self.assertEqual(self.client.get('/team/dashboard').json()['used_bytes'], 18)
        person = self.client.get('/team/people').json()['items'][0]
        self.assertEqual((person['used_bytes'], person['file_count']), (18, 1))
        self.assertEqual(self.client.get('/team/people?search=missing').json()['items'], [])
        self.connection.execute('UPDATE team SET manager_can_view_drives=true WHERE id=%s', (self.team_id,))
        visible = self.client.get('/team/dashboard').json()
        self.assertEqual(visible['recent_files'][0]['id'], str(fixture['id']))
        self.assertEqual(visible['largest_files'][0]['owner_name'], 'Owner')
        self.assertTrue(self.client.get('/team/people').json()['manager_can_view_drives'])
        self.connection.execute("UPDATE stored_file SET state='trashed' WHERE id=%s", (fixture['id'],))
        trashed = self.client.get('/team/dashboard').json()
        self.assertEqual((trashed['used_bytes'], trashed['files'], trashed['recent_files']), (18, 0, []))
        self.connection.execute("UPDATE team_account SET status='disabled' WHERE id=%s", (fixture['account_id'],))
        self.assertEqual(self.client.get('/team/dashboard').status_code, 401)

    def test_member_storage_usage(self):
        fixture = self.private_file_fixture()
        path = "/team/storage"
        initial = self.client.get(path)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json(), {"used_bytes": 7, "team_used_bytes": 7, "quota_bytes": 1000000000})
        self.assertIn("private, no-store", initial.headers["cache-control"])
        self.connection.execute("""INSERT INTO file_version(file_id,company_id,team_id,uploader_id,number,base_version,
            connection_id,object_key,name,size_bytes,quota_bytes,upload_key)
            VALUES (%s,%s,%s,%s,2,1,%s,'revision','revision.txt',11,11,%s)""",
            (fixture['id'], self.company_id, self.team_id, fixture['account_id'], fixture['storage_id'], uuid4()))
        self.assertEqual(self.client.get(path).json()['used_bytes'], 18)
        self.connection.execute("UPDATE stored_file SET state='trashed' WHERE id=%s", (fixture['id'],))
        self.assertEqual(self.client.get(path).json()['used_bytes'], 18)
        other_id = self.connection.execute("""INSERT INTO team_account(company_id,team_id,name,email,mobile,role,status,auth_type)
            VALUES (%s,%s,'Other',%s,'9876543210','member','active','otp') RETURNING id""",
            (self.company_id, self.team_id, f"other-{secrets.token_hex(8)}@example.com")).fetchone()['id']
        token = secrets.token_urlsafe(32)
        self.connection.execute("INSERT INTO team_session VALUES (%s,%s,clock_timestamp()+INTERVAL '1 hour')", (hash_token(token), other_id))
        self.client.cookies.set("mikan_team_session", token)
        self.assertEqual(self.client.get(path + f"?owner_id={fixture['account_id']}").json(),
                         {"used_bytes": 0, "team_used_bytes": 18, "quota_bytes": 1000000000})
        self.client.cookies.set("mikan_team_session", fixture['token'])
        self.connection.execute("UPDATE file_version SET quota_bytes=0 WHERE file_id=%s", (fixture['id'],))
        self.connection.execute("UPDATE stored_file SET state='purged' WHERE id=%s", (fixture['id'],))
        self.assertEqual(self.client.get(path).json()['used_bytes'], 0)
        self.connection.execute("UPDATE stored_file SET state='cancelled' WHERE id=%s", (fixture['id'],))
        self.assertEqual(self.client.get(path).json()['team_used_bytes'], 0)
        self.connection.execute("UPDATE team SET storage_quota_bytes=0 WHERE id=%s", (self.team_id,))
        self.assertEqual(self.client.get(path).json()['quota_bytes'], 0)
        self.private_file_fixture()
        self.assertEqual(self.client.get(path).json()['used_bytes'], 7)
        self.client.cookies.set("mikan_team_session", fixture['token'])
        self.assertEqual(self.client.get(path).json()['used_bytes'], 0)
        self.client.cookies.delete("mikan_team_session")
        self.assertEqual(self.client.get(path).status_code, 401)

    def test_team_profile_edit_and_photo(self):
        self.private_file_fixture()
        path = "/team/profile"
        original = self.client.get("/auth/team/me").json()
        payload = {"name": "Updated Member", "mobile": "+91 98765 43210"}
        self.assertEqual(self.client.get(path + "/photo").status_code, 404)
        self.assertEqual(self.client.post(path, json=payload, headers={"Origin": "https://evil.invalid"}).status_code, 403)
        for extra in ({"role": "manager"}, {"email": "other@example.com"}, {"team_id": 1}, {"company_id": 1}, {"name": " "}, {"mobile": "invalid"}, {"remove_photo": "true"}):
            self.assertEqual(self.client.post(path, json={**payload, **extra}).status_code, 422)
        output = io.BytesIO()
        Image.new("RGB", (800, 600), "green").save(output, format="JPEG")
        photo = base64.b64encode(output.getvalue()).decode()
        self.assertEqual(self.client.post(path, json={**payload, "photo_base64": photo, "remove_photo": True}).status_code, 422)
        result = self.client.post(path, json={**payload, "photo_base64": photo})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["has_photo"])
        for key in ("id", "email", "role", "auth_type", "team_id", "company_id"):
            self.assertEqual(result.json()[key], original[key])
        image = self.client.get(path + "/photo")
        self.assertEqual(image.headers["content-type"], "image/webp")
        self.assertEqual(image.headers["x-content-type-options"], "nosniff")
        self.assertIn("no-store", image.headers["cache-control"])
        with Image.open(io.BytesIO(image.content)) as decoded:
            self.assertLessEqual(max(decoded.size), 512)
            self.assertFalse(decoded.getexif())
        self.assertEqual(self.client.post(path, json=payload).status_code, 200)
        self.assertEqual(self.client.get(path + "/photo").content, image.content)
        for invalid in ("", "bad", base64.b64encode(b"<svg></svg>").decode(), "a" * 2796205):
            self.assertEqual(self.client.post(path, json={**payload, "name": "Must not save", "photo_base64": invalid}).status_code, 422)
        self.assertEqual(self.client.get("/auth/team/me").json()["name"], payload["name"])
        self.assertEqual(self.client.get(path + "/photo").content, image.content)
        self.assertEqual(self.client.post(path, json={**payload, "remove_photo": True}).status_code, 200)
        self.assertFalse(self.client.get("/auth/team/me").json()["has_photo"])
        self.assertEqual(self.client.get(path + "/photo").status_code, 404)
        self.client.post(path, json={**payload, "photo_base64": photo})
        self.private_file_fixture()
        self.assertEqual(self.client.get(path + "/photo").status_code, 404)
        self.assertNotEqual(self.client.get("/auth/team/me").json()["name"], payload["name"])
        self.client.cookies.delete("mikan_team_session")
        self.assertEqual(self.client.get(path + "/photo").status_code, 401)
        self.assertEqual(self.client.post(path, json=payload).status_code, 401)

    @patch('trash.storage_client')
    def test_trash_retention_and_restore(self, factory):
        from botocore.exceptions import ClientError
        from trash import purge_file
        fixture = self.private_file_fixture()
        identifier = fixture['id']
        path = f'/team/files/{identifier}'
        settings = '/company/teams/data/trash-settings'
        self.assertEqual(self.client.get(settings).json(), {'enabled': False, 'retention_days': 30})
        self.assertEqual(self.client.post(settings, json={'enabled': True, 'retention_days': 0}).status_code, 422)
        self.assertEqual(self.client.post(path + '/trash', json={}).status_code, 200)
        self.assertEqual(self.client.get(path + '/content').status_code, 404)
        self.assertEqual(self.client.get('/team/files/trash').json()['total'], 1)
        self.assertFalse(purge_file(self.connection, identifier))
        self.assertEqual(self.client.post(path + '/restore', json={}).status_code, 200)
        self.assertEqual(self.client.get('/team/files/trash').json()['total'], 0)
        self.assertEqual(self.client.post(settings, json={'enabled': True, 'retention_days': 30}).status_code, 200)
        self.assertEqual(self.client.post(path + '/trash', json={}).status_code, 200)
        self.assertFalse(purge_file(self.connection, identifier))
        self.connection.execute("UPDATE stored_file SET trashed_at=clock_timestamp()-INTERVAL '31 days' WHERE id=%s", (identifier,))
        client = factory.return_value
        client.list_multipart_uploads.return_value = {'Uploads': []}
        client.delete_object.side_effect = RuntimeError('provider unavailable')
        self.assertFalse(purge_file(self.connection, identifier))
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 7)
        self.assertEqual(self.client.post(path + '/restore', json={}).status_code, 409)
        client.delete_object.side_effect = None
        client.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        factory.reset_mock()
        self.assertFalse(purge_file(self.connection, identifier))
        factory.assert_not_called()
        self.connection.execute("UPDATE stored_file SET purge_next_attempt_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (identifier,))
        self.client.post(settings, json={'enabled': False, 'retention_days': 30})
        self.assertTrue(purge_file(self.connection, identifier))
        self.assertFalse(purge_file(self.connection, identifier))
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 0)
        self.assertEqual(self.client.get('/team/files/trash').json()['total'], 0)
        self.assertEqual(self.client.post(path + '/restore', json={}).status_code, 409)

    def test_trash_permissions_and_admin_restore(self):
        fixture = self.private_file_fixture()
        path = f"/team/files/{fixture['id']}"
        settings = '/company/teams/data/trash-settings'
        empty = '/company/teams/data/trash/empty'
        self.assertEqual(self.client.post(path + '/trash', json={}, headers={'Origin': 'https://evil.invalid'}).status_code, 403)
        self.assertEqual(self.client.post(empty, json={}, headers={'Origin': 'https://evil.invalid'}).status_code, 403)
        for payload in ({'enabled': 'true', 'retention_days': 30}, {'enabled': True, 'retention_days': 3651}, {'enabled': True, 'retention_days': 1.5}):
            self.assertEqual(self.client.post(settings, json=payload).status_code, 422)
        person = self.connection.execute("""INSERT INTO team_account(company_id,team_id,name,email,mobile,role,status,auth_type)
            VALUES (%s,%s,'Other',%s,'9876543210','manager','active','otp') RETURNING id""",
            (self.company_id, self.team_id, f'other-{secrets.token_hex(8)}@example.com')).fetchone()['id']
        token = secrets.token_urlsafe(32)
        self.connection.execute("INSERT INTO team_session VALUES (%s,%s,clock_timestamp()+INTERVAL '1 hour')", (hash_token(token), person))
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.post(path + '/trash', json={}).status_code, 404)
        self.assertEqual(self.client.post(path + '/restore', json={}).status_code, 404)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.assertEqual(self.client.post(path + '/trash', json={}).status_code, 200)
        stamp = self.client.get('/team/files/trash').json()['items'][0]['trashed_at']
        self.assertEqual(self.client.post(path + '/trash', json={}).status_code, 200)
        self.assertEqual(self.client.get('/team/files/trash').json()['items'][0]['trashed_at'], stamp)
        self.assertEqual(self.client.get('/team/files').json()['total'], 0)
        self.assertEqual(self.client.get('/team/files?folder=').json()['total'], 0)
        item = self.client.get('/company/teams/data/files?view=trash').json()['items'][0]
        self.assertTrue(item['restorable'])
        admin_path = f"/company/teams/data/files/{fixture['id']}"
        payload = {'action': 'restore', 'name': item['name'], 'folder': item['folder'], 'expected_name': item['name'], 'expected_folder': item['folder'], 'expected_state': 'trashed'}
        self.assertEqual(self.client.post(admin_path, json=payload).status_code, 200)
        self.assertEqual(self.client.post(admin_path, json={**payload, 'action': 'trash', 'expected_state': 'ready'}).status_code, 200)
        self.assertEqual(self.client.post(path + '/restore', json={}).status_code, 409)
        response = self.client.post(empty, json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'detail': 'Trash cleanup queued.', 'queued': 1})
        self.assertEqual(self.connection.execute('SELECT state FROM stored_file WHERE id=%s', (fixture['id'],)).fetchone()['state'], 'purging')
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.get('/team/files/trash').json()['total'], 0)
        self.client.cookies.delete('mikan_company_admin_session')
        self.assertEqual(self.client.get(settings).status_code, 401)
        self.assertEqual(self.client.post(settings, json={'enabled': True, 'retention_days': 1}).status_code, 401)
        self.assertEqual(self.client.post(empty, json={}).status_code, 401)
        self.client.cookies.delete('mikan_team_session')
        self.assertEqual(self.client.get('/team/files/trash').status_code, 401)
        self.assertEqual(self.client.post(path + '/trash', json={}).status_code, 401)
        self.private_file_fixture()
        self.assertEqual(self.client.post(path + '/restore', json={}).status_code, 404)
        self.assertEqual(self.client.post(path + '/trash', json={}).status_code, 404)
        self.assertEqual(self.client.post(admin_path, json=payload).status_code, 404)

    @patch('trash.storage_client')
    def test_trash_cleans_all_revisions_and_locks(self, factory):
        from botocore.exceptions import ClientError
        from trash import purge_file
        fixture = self.private_file_fixture()
        path = f"/team/files/{fixture['id']}"
        self.client.get(path + '/versions')
        second_store = self.client.post('/integrations/storage/r2', json={**fixture['payload'], 'name': 'Archived storage', 'bucket': 'archive-' + secrets.token_hex(8)}).json()['id']
        pending = None
        for number, key, size, charge, state, store in ((2, 'history/object', 5, 5, 'ready', second_store), (3, 'private/object', 7, 0, 'ready', fixture['storage_id']), (4, 'pending/object', 11, 11, 'pending', fixture['storage_id'])):
            pending = self.connection.execute("""INSERT INTO file_version(file_id,company_id,team_id,uploader_id,number,base_version,connection_id,
                object_key,name,size_bytes,quota_bytes,state,upload_key) VALUES (%s,%s,%s,%s,%s,1,%s,%s,'report.html',%s,%s,%s,%s) RETURNING id""",
                (fixture['id'], self.company_id, self.team_id, fixture['account_id'], number, store, key, size, charge, state, uuid4())).fetchone()['id']
        self.connection.execute("UPDATE file_version SET multipart_upload_id='unfinished' WHERE id=%s", (pending,))
        self.client.post('/company/teams/data/trash-settings', json={'enabled': True, 'retention_days': 1})
        self.client.post(path + '/trash', json={})
        self.connection.execute("UPDATE stored_file SET trashed_at=clock_timestamp()-INTERVAL '2 days' WHERE id=%s", (fixture['id'],))
        with connect() as other:
            other.autocommit = True
            other.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))', (f'upload-operation:{pending}',))
            self.assertFalse(purge_file(self.connection, fixture['id']))
        factory.assert_not_called()
        self.assertEqual(self.connection.execute('SELECT state FROM stored_file WHERE id=%s', (fixture['id'],)).fetchone()['state'], 'trashed')
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id=%s', (second_store,))
        client = factory.return_value
        client.list_multipart_uploads.return_value = {'Uploads': []}
        client.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 23)
        self.assertTrue(purge_file(self.connection, fixture['id']))
        self.assertCountEqual([call.kwargs['Key'] for call in client.delete_object.call_args_list], ['private/object', 'history/object', 'pending/object'])
        client.abort_multipart_upload.assert_called_once()
        self.assertEqual(client.abort_multipart_upload.call_args.kwargs['UploadId'], 'unfinished')
        self.assertEqual({call.args[0]['id'] for call in factory.call_args_list}, {fixture['storage_id'], second_store})
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) AS total FROM file_version WHERE file_id=%s AND (quota_bytes<>0 OR state<>'cancelled')", (fixture['id'],)).fetchone()['total'], 0)
        self.assertEqual(self.client.get('/company/teams/data/files').json()['total'], 0)
        self.assertEqual(self.client.get('/company/teams/data/files?view=trash').json()['total'], 0)
        self.assertEqual(self.client.get(path + '/versions').status_code, 404)
        self.assertFalse(purge_file(self.connection, fixture['id']))

    def test_trash_worker_selection(self):
        from trash import cleanup_trash
        fixture = self.private_file_fixture()
        path = f"/team/files/{fixture['id']}"
        settings = '/company/teams/data/trash-settings'
        self.client.post(path + '/trash', json={})
        @contextmanager
        def database():
            yield self.connection
        with patch('trash.transfer_connection', database), patch('trash.purge_file') as purge:
            cleanup_trash()
            purge.assert_not_called()
            self.client.post(settings, json={'enabled': True, 'retention_days': 30})
            cleanup_trash()
            purge.assert_not_called()
            self.connection.execute("UPDATE stored_file SET trashed_at=clock_timestamp()-INTERVAL '2 days' WHERE id=%s", (fixture['id'],))
            self.client.post(settings, json={'enabled': True, 'retention_days': 1})
            cleanup_trash()
            purge.assert_called_once_with(self.connection, fixture['id'])
            purge.reset_mock()
            self.client.post(settings, json={'enabled': False, 'retention_days': 1})
            cleanup_trash()
            purge.assert_not_called()
            self.connection.execute("UPDATE stored_file SET state='purging',purge_next_attempt_at=clock_timestamp()+INTERVAL '15 minutes' WHERE id=%s", (fixture['id'],))
            cleanup_trash()
            purge.assert_not_called()
            self.connection.execute("UPDATE stored_file SET purge_next_attempt_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (fixture['id'],))
            cleanup_trash()
            purge.assert_called_once_with(self.connection, fixture['id'])

    def test_trash_worker_survives_other_worker_failure(self):
        from main import lifespan
        async def exercise():
            completed = asyncio.Event()
            loop = asyncio.get_running_loop()
            def failed():
                raise RuntimeError('unavailable')
            def cleaned():
                loop.call_soon_threadsafe(completed.set)
            with patch.dict('os.environ', {'MIKAN_AUTOMATION_ENABLED': 'true'}), patch('main.deliver_share_emails', failed), patch('main.tick', failed), patch('main.cleanup_trash', cleaned):
                with self.assertLogs('main', level='WARNING'):
                    async with lifespan(app):
                        await asyncio.wait_for(completed.wait(), timeout=5)
        asyncio.run(exercise())

    def test_lifespan_runs_three_migration_workers_concurrently(self):
        from main import lifespan
        async def exercise():
            started = 0
            all_started = asyncio.Event()
            release = asyncio.Event()
            loop = asyncio.get_running_loop()

            def migration():
                nonlocal started
                started += 1
                if started == 3:
                    loop.call_soon_threadsafe(all_started.set)
                asyncio.run_coroutine_threadsafe(release.wait(), loop).result(timeout=5)

            with patch.dict('os.environ', {'MIKAN_AUTOMATION_ENABLED': 'true', 'MIKAN_MIGRATION_WORKERS': '3'}), \
                    patch('main.deliver_share_emails'), patch('main.tick'), patch('main.cleanup_trash'), \
                    patch('main.process_migrations', migration):
                async with lifespan(app):
                    await asyncio.wait_for(all_started.wait(), timeout=5)
                    self.assertEqual(started, 3)
                    release.set()
        asyncio.run(exercise())

    def test_workflow_participant_notifications(self):
        fixture, reviewer, token, flow, payload = self.workflow_fixture()
        response = self.client.post(f"/team/workflows/{flow['id']}/submit", json={'file_id': str(fixture['id']), 'request_key': str(uuid4()), 'reviewers': {'review': [reviewer]}})
        self.assertEqual(response.status_code, 201, response.text)
        identifier = response.json()['id']
        self.assertIn('submitted', self.client.get('/team/notifications').json()['items'][0]['message'])
        company_notice = self.client.get('/company/notifications?kind=workflow').json()['items'][0]
        self.assertEqual(company_notice['run_id'], identifier)
        self.assertEqual(self.client.get('/admin/notifications?kind=workflow').json()['total'], 0)
        self.assertEqual(self.client.post(f"/team/notifications/{company_notice['id']}/read", json={'read': True}).status_code, 404)
        self.assertEqual(self.client.post(f"/company/notifications/{company_notice['id']}/read", json={'read': True}).status_code, 200)
        self.assertEqual(self.client.get('/team/notifications/unread').json()['unread'], 1)
        self.client.cookies.set('mikan_team_session', token)
        decision = self.client.post(f'/team/workflows/runs/{identifier}/decide', json={'outcome': 'approved', 'comment': ''})
        self.assertEqual(decision.status_code, 200, decision.text)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        messages = [row['message'] for row in self.client.get('/team/notifications').json()['items']]
        self.assertTrue(any('approved:' in message for message in messages))
        response = self.client.post(f"/team/workflows/{flow['id']}/submit", json={'file_id': str(fixture['id']), 'request_key': str(uuid4()), 'reviewers': {'review': [reviewer]}})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.client.post(f"/team/workflows/runs/{response.json()['id']}/cancel", json={}).status_code, 200)
        self.client.cookies.set('mikan_team_session', token)
        self.assertIn('cancelled', self.client.get('/team/notifications').json()['items'][0]['message'])
        self.assertEqual(self.client.get('/company/notifications?kind=storage').json()['total'], 0)
        self.assertEqual(self.client.get('/company/notifications?kind=workflow').json()['total'], 5)

    def test_automation_failure_notifications(self):
        from automation import enqueue, process_one
        fixture, reviewer, token, flow, payload = self.workflow_fixture()
        enqueue(self.connection, {**flow, 'company_id': self.company_id}, 'trigger', str(uuid4()), {}, fixture['id'])
        job = self.connection.execute('SELECT * FROM automation_job WHERE file_id=%s', (fixture['id'],)).fetchone()
        with patch('automation.execute_job', side_effect=RuntimeError('private-provider-secret')):
            self.assertTrue(process_one(self.connection))
            self.assertEqual(self.client.get('/company/notifications?kind=automation_failure').json()['total'], 0)
            self.connection.execute("UPDATE automation_job SET attempts=4,next_at=clock_timestamp() WHERE id=%s", (job['id'],))
            self.assertTrue(process_one(self.connection))
            for path in ('/company/notifications', '/team/notifications'):
                response = self.client.get(path + '?kind=automation_failure')
                self.assertEqual(response.json()['total'], 1)
                self.assertNotIn('private-provider-secret', response.text)
                self.assertIsNone(response.json()['items'][0]['run_id'])
            self.assertEqual(self.client.get('/admin/notifications').json()['total'], 0)
            self.connection.execute("UPDATE automation_job SET status='retry',attempts=4,next_at=clock_timestamp() WHERE id=%s", (job['id'],))
            self.assertTrue(process_one(self.connection))
            self.assertEqual(self.client.get('/team/notifications?kind=automation_failure').json()['total'], 1)
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.get('/team/notifications?kind=automation_failure').json()['total'], 0)

    def test_notification_stream_scope_and_revocation(self):
        from notifications import notification_events

        class Listener:
            def __init__(self):
                self.closed = False
                self.payloads = iter(('team:99', 'team:7', 'team:7'))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def execute(self, query):
                self.query = query

            async def notifies(self, **kwargs):
                yield Mock(payload=next(self.payloads))

        async def collect():
            request = Mock(is_disconnected=AsyncMock(return_value=False))
            account = {'id': 7, 'company_id': 2, 'team_id': 3, 'role': 'member'}
            listener = Listener()
            with patch('notifications.connect_async', AsyncMock(return_value=listener)), patch('notifications.stream_account', side_effect=[account, account, HTTPException(401)]):
                events = [event async for event in notification_events(request, Mock(), 'team', account)]
            self.assertEqual(listener.query, 'LISTEN mikan_notifications')
            self.assertTrue(listener.closed)
            self.assertIn('changed', events[0])
            self.assertEqual(events[1], ': heartbeat\n\n')
            self.assertIn('changed', events[2])
            self.assertIn('expired', events[3])
            self.assertNotIn('team:99', ''.join(events))
            listener = Listener()
            with patch('notifications.connect_async', AsyncMock(return_value=listener)), patch('notifications.stream_account', return_value={**account, 'team_id': 4}):
                events = [event async for event in notification_events(request, Mock(), 'team', account)]
            self.assertIn('expired', events[-1])
            self.assertTrue(listener.closed)

        asyncio.run(collect())
        for scope in ('admin', 'company', 'team'):
            self.assertEqual(self.client.get(f'/{scope}/notifications/stream').status_code, 401)

    def test_storage_notification_thresholds_and_scopes(self):
        self.private_file_fixture()
        self.connection.execute('UPDATE team SET storage_quota_bytes=10 WHERE id=%s', (self.team_id,))
        paths = ['/admin/notifications', '/company/notifications', '/team/notifications']
        for base in paths:
            self.assertEqual(self.client.get(base + '/unread').json()['unread'], 0)
        self.connection.execute('UPDATE team SET storage_quota_bytes=8 WHERE id=%s', (self.team_id,))
        for base in paths:
            response = self.client.get(base)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['unread'], 1)
            self.assertEqual(response.json()['items'][0]['kind'], 'storage_warning')
            self.assertIn('private, no-store', response.headers['cache-control'])
        self.connection.execute('UPDATE team SET storage_quota_bytes=8 WHERE id=%s', (self.team_id,))
        self.assertEqual(self.client.get(paths[0]).json()['total'], 1)
        self.connection.execute('UPDATE team SET storage_quota_bytes=7 WHERE id=%s', (self.team_id,))
        self.assertEqual(self.client.get(paths[2]).json()['items'][0]['kind'], 'storage_full')
        notice = self.client.get(paths[2]).json()['items'][0]['id']
        self.assertEqual(self.client.post(paths[1] + f'/{notice}/read', json={'read': True}).status_code, 404)
        self.assertEqual(self.client.post(paths[2] + f'/{notice}/read', json={'read': True}, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post(paths[2] + f'/{notice}/read', json={'read': True}).status_code, 200)
        self.assertEqual(self.client.get(paths[2] + '?view=read').json()['total'], 1)
        self.assertEqual(self.client.post(paths[2] + f'/{notice}/read', json={'read': False}).status_code, 200)
        snapshot = self.client.get(paths[2]).json()['before']
        self.connection.execute('UPDATE team SET storage_quota_bytes=10 WHERE id=%s', (self.team_id,))
        self.connection.execute('UPDATE team SET storage_quota_bytes=8 WHERE id=%s', (self.team_id,))
        self.assertEqual(self.client.post(paths[2] + '/read-all', json={'before': snapshot}).status_code, 200)
        self.assertEqual(self.client.get(paths[2] + '/unread').json()['unread'], 1)
        self.assertEqual(self.client.get(paths[1] + '/unread').json()['unread'], 3)
        self.assertEqual(self.client.get(paths[2] + '?from_date=2026-10-05&to_date=2026-10-01').status_code, 422)
        self.assertEqual(self.client.post(paths[2] + '/read-all', json={'before': '2099-01-01T00:00:00Z'}).status_code, 422)
        for cookie, base in zip(('mikan_admin_session', 'mikan_company_admin_session', 'mikan_team_session'), paths):
            self.client.cookies.delete(cookie)
            denied = self.client.get(base)
            self.assertEqual(denied.status_code, 401)
            self.assertIn('private, no-store', denied.headers['cache-control'])

    def test_notification_settings_and_workflow_compatibility(self):
        fixture, reviewer, token, flow, payload = self.workflow_fixture()
        folder_path = f'/company/teams/folders/{self.team_id}'
        settings = {'storage_quota_bytes': 10, 'manager_can_view_drives': False,
                    'storage_alerts_enabled': True, 'storage_warning_percent': 50, 'storage_critical_percent': 70}
        self.assertEqual(self.client.post(folder_path, json=settings).status_code, 200)
        self.assertEqual(self.client.get('/team/notifications').json()['items'][0]['kind'], 'storage_critical')
        for invalid in ({'storage_warning_percent': 70}, {'storage_warning_percent': 0}, {'storage_critical_percent': 100}, {'storage_alerts_enabled': 'true'}):
            self.assertEqual(self.client.post(folder_path, json={**settings, **invalid}).status_code, 422)
        self.assertEqual(self.client.post(folder_path, json={**settings, 'storage_alerts_enabled': False}).status_code, 200)
        self.assertEqual(self.client.post(folder_path, json={'storage_quota_bytes': 7, 'manager_can_view_drives': False}).status_code, 200)
        self.assertEqual(self.client.get('/team/notifications').json()['total'], 1)
        folder = next(row for row in self.client.get('/company/teams/folders').json()['items'] if row['id'] == self.team_id)
        self.assertFalse(folder['storage_alerts_enabled'])
        self.assertEqual(folder['storage_warning_percent'], 50)
        self.assertEqual(self.client.post(folder_path, json={**settings, 'storage_quota_bytes': 7}).status_code, 200)
        self.assertEqual(self.client.get('/team/notifications').json()['total'], 2)
        own = self.client.get('/team/notifications').json()['items'][0]['id']
        response = self.client.post(f"/team/workflows/{flow['id']}/submit", json={'file_id': str(fixture['id']), 'request_key': str(uuid4()), 'reviewers': {'review': [reviewer]}})
        self.assertEqual(response.status_code, 201, response.text)
        self.client.cookies.set('mikan_team_session', token)
        notices = self.client.get('/team/notifications?kind=workflow').json()
        self.assertEqual(notices['total'], 1)
        self.assertEqual(self.client.post(f'/team/notifications/{own}/read', json={'read': True}).status_code, 404)
        identifier = notices['items'][0]['id']
        self.assertEqual(self.client.post(f'/team/notifications/{identifier}/read', json={'read': True}).status_code, 200)
        self.assertIsNotNone(self.client.get('/team/workflows/notifications').json()['items'][0]['read_at'])
        old_id = identifier.split(':')[1]
        self.connection.execute('UPDATE workflow_notification SET read_at=NULL WHERE id=%s', (int(old_id),))
        self.assertEqual(self.client.post(f'/team/workflows/notifications/{old_id}/read', json={}).status_code, 200)
        self.assertEqual(self.client.get('/team/notifications/unread').json()['unread'], 0)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        other_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'New team') RETURNING id", (self.company_id,)).fetchone()['id']
        self.connection.execute('UPDATE team_account SET team_id=%s WHERE id=%s', (other_team, fixture['account_id']))
        self.assertEqual(self.client.get('/team/notifications?kind=storage').json()['total'], 0)
        self.assertEqual(self.client.post(f'/team/notifications/{own}/read', json={'read': True}).status_code, 404)

    def test_company_storage_dashboard(self):
        fixture = self.private_file_fixture()
        base = '/company/teams/insights'
        self.connection.execute("""INSERT INTO file_version(file_id,company_id,team_id,uploader_id,number,base_version,
            connection_id,object_key,name,size_bytes,quota_bytes,upload_key)
            VALUES (%s,%s,%s,%s,2,1,%s,'dashboard-revision','revision.txt',11,11,%s)""",
            (fixture['id'], self.company_id, self.team_id, fixture['account_id'], fixture['storage_id'], uuid4()))
        foreign = self.client.post('/companies/', json=self.company_payload()).json()['id']
        self.connection.execute("INSERT INTO team(company_id,name,storage_quota_bytes) VALUES (%s,'Foreign secret',12345)", (foreign,))
        response = self.client.get(base + '/storage')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('private, no-store', response.headers['cache-control'])
        summary = response.json()
        self.assertEqual(summary['capacity_bytes'], 16000000000000)
        self.assertEqual(summary['used_bytes'], 18)
        self.assertEqual(summary['remaining_bytes'], 16000000000000 - 18)
        self.assertNotIn('Foreign secret', response.text)
        members = summary['previews']['storage-employees']
        self.assertEqual(next(row['used_bytes'] for row in members if row['name'] == 'Owner'), 18)
        self.assertEqual(summary['previews']['largest-files'][0]['size_bytes'], 7)
        for report in ('storage-teams', 'storage-employees', 'largest-files'):
            result = self.client.get(base + '/reports/' + report + '?sort=usage')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(all(row['company_id'] == self.company_id for row in result.json()['items']))
        self.assertEqual(self.client.get(base + '/reports/largest-files?search=missing').json()['total'], 0)
        self.assertEqual(self.client.get(base + '/reports/largest-files?from_date=2000-01-01&to_date=2000-01-02').json()['total'], 0)
        self.assertEqual(self.client.get(base + '/reports/largest-files?export=true').status_code, 200)
        self.assertEqual(self.client.get(base + f'/storage?company_id={foreign}').status_code, 403)
        self.connection.execute('UPDATE file_version SET quota_bytes=0 WHERE file_id=%s', (fixture['id'],))
        self.connection.execute("UPDATE stored_file SET state='purged' WHERE id=%s", (fixture['id'],))
        self.connection.execute('UPDATE team SET storage_quota_bytes=0 WHERE company_id=%s', (self.company_id,))
        empty = self.client.get(base + '/storage').json()
        self.assertEqual(empty['used_bytes'], 0)
        self.assertEqual(empty['capacity_bytes'], 16000000000000)
        self.assertEqual(empty['remaining_bytes'], 16000000000000)
        self.assertEqual(empty['previews']['largest-files'], [])
        self.client.cookies.delete('mikan_company_admin_session')
        self.assertEqual(self.client.get(base + '/storage').status_code, 401)

    def test_company_storage_preview_ranking(self):
        fixture = self.private_file_fixture()
        base = '/company/teams/insights'
        for index in range(12):
            team_id = self.connection.execute("INSERT INTO team(company_id,name,storage_quota_bytes) VALUES (%s,%s,10000) RETURNING id",
                (self.company_id, f'Team {index:02d}')).fetchone()['id']
            owner_id = self.connection.execute("""INSERT INTO team_account(company_id,team_id,name,email,mobile,role,status,auth_type)
                VALUES (%s,%s,%s,%s,'9876543210','member','active','otp') RETURNING id""",
                (self.company_id, team_id, f'Member {index:02d}', f'ranking-{index}-{secrets.token_hex(8)}@example.com')).fetchone()['id']
            self.connection.execute("""INSERT INTO stored_file(company_id,team_id,owner_id,connection_id,object_key,name,size_bytes,state,etag)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'ready','test-etag')""",
                (self.company_id, team_id, owner_id, fixture['storage_id'], f'ranking/{index}', f'File {index:02d}', 100 + index // 2))
        summary = self.client.get(base + '/storage').json()
        self.assertEqual(summary['teams'], 13)
        for report, amount in (('storage-teams', 'used_bytes'), ('storage-employees', 'used_bytes'), ('largest-files', 'size_bytes')):
            preview = summary['previews'][report]
            first = self.client.get(base + f'/reports/{report}?sort=usage').json()
            second = self.client.get(base + f'/reports/{report}?sort=usage&page=2').json()
            self.assertEqual(len(preview), 5)
            self.assertEqual([row['id'] for row in preview], [row['id'] for row in first['items'][:5]])
            self.assertEqual(len(first['items']), 10)
            self.assertEqual(len(second['items']), first['total'] - 10)
            self.assertFalse({row['id'] for row in first['items']} & {row['id'] for row in second['items']})
            amounts = [row[amount] for row in first['items'] + second['items']]
            self.assertEqual(amounts, sorted(amounts, reverse=True))
            self.assertEqual(self.client.get(base + f'/reports/{report}?sort=usage').json()['items'], first['items'])
        self.assertEqual(self.client.get(base + '/reports/largest-files?search=File%2009').json()['total'], 1)

    def test_insights_scopes_reports_and_csv(self):
        fixture = self.private_file_fixture()
        foreign = self.client.post('/companies/', json=self.company_payload()).json()['id']
        for company_id, subject in ((self.company_id, '=SUM(1,2)'), (foreign, 'Foreign secret')):
            self.connection.execute('INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,%s,%s,%s)',
                                    (company_id, 'Tester', 'file_edit', subject, 'Report test'))
        company_base = '/company/teams/insights'
        admin_base = '/admin/insights'
        response = self.client.get(company_base + '/audit?source=data')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['items'][0]['company_id'], self.company_id)
        self.assertEqual(self.client.get(company_base + f'/audit?company_id={foreign}').status_code, 403)
        self.assertEqual(self.client.get(admin_base + f'/audit?source=data&company_id={foreign}').json()['total'], 1)
        export = self.client.get(company_base + '/audit?source=data&export=true')
        self.assertIn('attachment;', export.headers['content-disposition'])
        self.assertIn('private, no-store', export.headers['cache-control'])
        self.assertIn("'=SUM(1,2)", export.text)
        self.assertNotIn('Foreign secret', export.text)
        self.assertEqual(self.client.get(company_base + '/audit?from_date=2026-10-05&to_date=2026-10-01').status_code, 422)
        for report in ('storage-teams', 'storage-employees', 'activity', 'workflows', 'automation'):
            response = self.client.get(company_base + '/reports/' + report)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(all(row['company_id'] == self.company_id for row in response.json()['items']))
            self.assertEqual(self.client.get(company_base + '/reports/' + report + '?export=true').status_code, 200)
            self.assertEqual(self.client.get(admin_base + f'/reports/{report}?company_id={foreign}').status_code, 200)
        usage = self.client.get(company_base + '/reports/storage-employees').json()['items']
        self.assertEqual(next(row['used_bytes'] for row in usage if row['name'] == 'Owner'), 7)
        self.assertEqual(self.client.get(company_base + '/reports/storage-teams?from_date=2026-01-01').status_code, 422)
        self.assertEqual(self.client.get(company_base + '/reports/unknown').status_code, 422)
        self.assertEqual(self.client.get(company_base + '/audit?page=0').status_code, 422)
        with patch('insights.EXPORT_LIMIT', 0):
            self.assertEqual(self.client.get(company_base + '/audit?export=true').status_code, 413)
        self.client.cookies.delete('mikan_admin_session')
        self.assertEqual(self.client.get(admin_base + '/audit').status_code, 401)
        self.assertEqual(self.client.get(company_base + '/audit').status_code, 200)
        self.client.cookies.delete('mikan_company_admin_session')
        denied = self.client.get(company_base + '/audit')
        self.assertEqual(denied.status_code, 401)
        self.assertIn('private, no-store', denied.headers['cache-control'])
        self.assertIn('private, no-store', self.client.get(admin_base + '/audit').headers['cache-control'])
        self.assertEqual(self.client.get(company_base + '/reports/storage-teams?export=true').status_code, 401)

    def test_insights_date_pagination_and_storage_accounting(self):
        fixture = self.private_file_fixture()
        for index in range(12):
            self.connection.execute("INSERT INTO data_activity(company_id,actor,action,subject,detail,created_at) VALUES (%s,'Tester','file_edit',%s,'Date test','2026-01-10 18:29:59+00')",
                                    (self.company_id, f'Entry {index}'))
        self.connection.execute("INSERT INTO data_activity(company_id,actor,action,subject,detail,created_at) VALUES (%s,'Tester','file_edit','Outside','Date test','2026-01-10 18:30:00+00')", (self.company_id,))
        self.connection.execute("INSERT INTO data_activity(company_id,actor,action,subject,detail,created_at) VALUES (%s,'Tester','file_edit','Before','Date test','2026-01-09 18:29:59+00')", (self.company_id,))
        self.connection.execute("UPDATE data_activity SET created_at='2026-01-09 18:30:00+00' WHERE company_id=%s AND subject='Entry 0'", (self.company_id,))
        base = '/company/teams/insights'
        query = '/audit?source=data&from_date=2026-01-10&to_date=2026-01-10'
        first = self.client.get(base + query).json()
        second = self.client.get(base + query + '&page=2').json()
        self.assertEqual(first['total'], 12)
        self.assertEqual(len(first['items']), 10)
        self.assertEqual(len(second['items']), 2)
        self.assertFalse({row['id'] for row in first['items']} & {row['id'] for row in second['items']})
        self.assertEqual(self.client.get(base + query).json()['items'], first['items'])
        self.assertEqual(self.client.get(base + query + '&search=Entry%2011').json()['total'], 1)
        self.assertEqual(self.client.get(base + query + '&action=folder_create').json()['total'], 0)
        self.assertNotIn('Outside', self.client.get(base + query + '&export=true').text)
        exported = self.client.get(base + query + '&export=true').text
        self.assertNotIn('Before', exported)
        self.assertIn('2026-01-10T00:00:00+05:30', exported)
        self.assertIn('2026-01-10T23:59:59+05:30', exported)
        self.connection.execute("UPDATE stored_file SET state='trashed' WHERE id=%s", (fixture['id'],))
        other_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'New team') RETURNING id", (self.company_id,)).fetchone()['id']
        self.connection.execute('UPDATE team_account SET team_id=%s WHERE id=%s', (other_team, fixture['account_id']))
        teams = self.client.get(base + '/reports/storage-teams').json()['items']
        employees = self.client.get(base + '/reports/storage-employees').json()['items']
        self.assertEqual(sum(row['used_bytes'] for row in teams), 7)
        self.assertEqual(sum(row['used_bytes'] for row in employees), 7)
        self.assertEqual(sum(row['trash_bytes'] for row in employees), 7)
        self.assertEqual(sum(row['files'] for row in employees), 1)
        self.assertEqual(len([row for row in employees if row['name'] == 'Owner']), 2)
        from insights import csv_cell
        for value in ('=SUM(1,2)', ' +1', '-2', '@SUM(A1)', '\ttext', '\rtext', '\ntext'):
            self.assertEqual(csv_cell(value), "'" + value)
        self.assertEqual(csv_cell(123), 123)
        self.assertEqual(csv_cell('normal'), 'normal')

    def test_insights_workflow_outcomes(self):
        fixture, reviewer, token, flow, payload = self.workflow_fixture()
        submitted = self.client.post(f"/team/workflows/{flow['id']}/submit", json={
            'file_id': str(fixture['id']), 'request_key': str(uuid4()), 'reviewers': {'review': [reviewer]}})
        self.assertEqual(submitted.status_code, 201, submitted.text)
        base = '/company/teams/insights'
        initial = self.client.get(base + '/reports/workflows').json()['items'][0]
        self.assertEqual((initial['status'], initial['approvals'], initial['pending_reviews']), ('pending', 0, 1))
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.post(f"/team/workflows/runs/{submitted.json()['id']}/decide", json={'outcome': 'approved'}).status_code, 200)
        final = self.client.get(base + '/reports/workflows').json()['items'][0]
        self.assertEqual((final['status'], final['approvals'], final['pending_reviews']), ('approved', 1, 0))
        self.connection.execute("INSERT INTO automation_job(company_id,workflow_id,kind,dedupe_key,payload,status,attempts,last_error) VALUES (%s,%s,'webhook',%s,'{\"secret\":\"hidden\"}','failed',5,'Private error')",
                                (self.company_id, flow['id'], str(uuid4())))
        response = self.client.get(base + '/reports/automation?search=failed')
        self.assertEqual(response.json()['total'], 1)
        self.assertEqual(response.json()['items'][0]['attempts'], 5)
        self.assertNotIn('Private error', response.text)
        self.assertNotIn('hidden', response.text)
        audit = self.client.get(base + '/audit?source=workflows&action=approved').json()
        self.assertEqual(audit['total'], 1)
        self.assertEqual(audit['items'][0]['actor'], 'Reviewer')
        foreign = self.client.post('/companies/', json=self.company_payload()).json()['id']
        for path in ('/reports/workflows', '/reports/automation', '/audit'):
            self.assertEqual(self.client.get('/admin/insights' + path + f'?company_id={foreign}').json()['total'], 0)

    @patch('files.boto3.client')
    def test_data_administration_files_and_download(self, factory):
        fixture = self.private_file_fixture()
        base = '/company/teams/data'
        listing = self.client.get(base + '/files')
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertIn('private, no-store', listing.headers['Cache-Control'])
        item = next(row for row in listing.json()['items'] if row['id'] == str(fixture['id']))
        self.assertFalse({'connection_id','provider','object_key','etag'} & item.keys())
        payload = {'action': 'edit', 'name': 'renamed.txt', 'folder': 'Reports/2026',
                   'expected_name': item['name'], 'expected_folder': item['folder'], 'expected_state': item['state']}
        path = base + f"/files/{fixture['id']}"
        self.assertEqual(self.client.post(path, json=payload, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post(path, json={**payload, 'name': '../bad'}).status_code, 422)
        self.assertEqual(self.client.post(path, json=payload).status_code, 200)
        self.assertEqual(self.client.post(path, json=payload).status_code, 409)
        current = {**payload, 'expected_name': 'renamed.txt', 'expected_folder': 'Reports/2026'}
        factory.return_value.get_object.return_value = {'Body': io.BytesIO(b'private'), 'ContentLength': 7, 'ETag': '"test-etag"'}
        response = self.client.get(path + '/content')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, b'private')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertEqual(self.client.post(path, json={**current, 'action': 'trash'}).status_code, 200)
        self.assertEqual(self.client.get(fixture['path']).status_code, 404)
        self.assertEqual(self.client.get(path + '/content').status_code, 404)
        self.assertEqual(self.client.get(base + '/files?view=trash').json()['total'], 1)
        usage = self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes']
        self.assertEqual(usage, 7)
        self.assertEqual(self.client.post(path, json={**current, 'action': 'restore', 'expected_state': 'trashed'}).status_code, 200)
        row = self.connection.execute('SELECT object_key,owner_id,connection_id FROM stored_file WHERE id=%s', (fixture['id'],)).fetchone()
        self.assertEqual(row, {'object_key': 'private/object', 'owner_id': fixture['account_id'], 'connection_id': fixture['storage_id']})
        self.assertGreaterEqual(self.client.get(base + '/activity').json()['total'], 4)
        self.assertEqual(self.client.get(base + '/files?from_date=2026-10-05&to_date=2026-10-01').status_code, 422)
        self.client.cookies.delete('mikan_company_admin_session')
        self.assertEqual(self.client.get(base + '/files').status_code, 401)
        denied = self.client.get(path + '/content')
        self.assertEqual(denied.status_code, 401)
        self.assertIn('private, no-store', denied.headers['Cache-Control'])
        self.assertEqual(denied.headers['CDN-Cache-Control'], 'no-store')
        self.client.cookies.clear()
        self.prepare_team()
        self.assertEqual(self.client.get(base + '/files').json()['total'], 0)
        self.assertEqual(self.client.get(base + '/folders').json()['total'], 0)
        self.assertEqual(self.client.get(base + '/activity').json()['total'], 0)
        self.assertEqual(self.client.get(path + '/content').status_code, 404)
        self.assertEqual(self.client.post(path, json=current).status_code, 404)

    def test_member_folder_creation(self):
        fixture = self.private_file_fixture()
        route = '/team/files/folders'
        self.assertIsNone(self.client.get('/team/files').json()['policy'])
        created = self.client.post(route, json={'name': 'Member folder'})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()['path'], 'Member folder')
        self.assertEqual(self.client.post(route, json={'name': 'Member folder'}).status_code, 409)
        self.assertEqual(self.client.post(route, json={'name': 'Nested', 'parent': 'Member folder'}).status_code, 201)
        self.assertEqual(self.client.get('/team/files', params={'folder': 'Member folder'}).json()['items'][0]['path'], 'Member folder/Nested')
        self.assertEqual(self.client.get('/company/teams/data/folders').json()['total'], 2)
        self.assertEqual(self.client.post(route, json={'name': '../invalid'}).status_code, 422)
        self.assertEqual(self.client.post(route, json={'name': 'Nested', 'parent': 'Missing'}).status_code, 404)
        self.assertEqual(self.client.post(route, json={'name': 'Other', 'owner_id': fixture['account_id']}).status_code, 422)
        self.assertEqual(self.client.post(route, json={'name': 'Blocked'}, headers={'Origin': 'https://untrusted.invalid'}).status_code, 403)
        self.assertEqual(self.client.post(route, json={'name': 'a' * 255}).status_code, 201)
        self.assertEqual(self.client.post(route, json={'name': 'b', 'parent': 'a' * 255}).status_code, 422)
        self.assertEqual(self.connection.execute("SELECT count(*) AS total FROM data_activity WHERE company_id=%s AND action='folder_create'", (self.company_id,)).fetchone()['total'], 3)
        self.client.cookies.delete('mikan_team_session')
        self.assertEqual(self.client.post(route, json={'name': 'Blocked'}).status_code, 401)

    def test_member_folder_browsing(self):
        fixture = self.private_file_fixture()
        base = '/company/teams/data/folders'
        folder = {'team_id': self.team_id, 'owner_id': fixture['account_id'], 'path': 'Reports', 'action': 'create'}
        self.assertEqual(self.client.post(base, json=folder).status_code, 200)
        root = self.client.get('/team/files?folder=')
        self.assertEqual(root.status_code, 200, root.text)
        self.assertTrue(any(row.get('kind') == 'folder' and row['path'] == 'Reports' for row in root.json()['items']))
        self.assertEqual(self.client.get('/team/files?folder=Reports').json()['total'], 0)
        self.connection.execute('UPDATE stored_file SET folder=%s WHERE id=%s', ('Reports/2026', fixture['id']))
        nested = self.client.get('/team/files?folder=Reports').json()
        self.assertEqual([(row['kind'], row['path']) for row in nested['items']], [('folder', 'Reports/2026')])
        files = self.client.get('/team/files?folder=Reports%2F2026').json()
        self.assertEqual(files['items'][0]['id'], str(fixture['id']))
        self.assertEqual(files['items'][0]['kind'], 'file')
        self.assertEqual(self.client.get('/team/files').json()['total'], 1)
        self.assertEqual(self.client.post(base, json={**folder, 'action': 'rename', 'target': 'Archive'}).status_code, 200)
        self.assertEqual(self.client.get('/team/files?folder=Reports').status_code, 404)
        self.assertEqual(self.client.get('/team/files?folder=Archive%2F2026').json()['items'][0]['folder'], 'Archive/2026')
        empty = {**folder, 'path': 'Empty'}
        self.assertEqual(self.client.post(base, json=empty).status_code, 200)
        self.assertEqual(self.client.post(base, json={**empty, 'action': 'remove'}).status_code, 200)
        self.assertEqual(self.client.get('/team/files?folder=Empty').status_code, 404)
        self.assertEqual(self.client.get('/team/files?folder=../invalid').status_code, 422)
        self.assertEqual(self.client.get('/team/files?folder=&from_date=2026-10-05&to_date=2026-10-01').status_code, 422)
        self.assertEqual(self.client.get('/team/files?folder=&from_date=9999-01-01').json()['total'], 0)
        special = {**folder, 'path': 'Q1 & 100%_+'}
        self.assertEqual(self.client.post(base, json=special).status_code, 200)
        self.assertEqual(self.client.get('/team/files', params={'folder': special['path']}).json()['total'], 0)
        self.assertEqual(self.client.get('/team/files', params={'folder': '', 'search': special['path']}).json()['total'], 1)
        self.connection.execute("UPDATE stored_file SET state='trashed' WHERE id=%s", (fixture['id'],))
        self.assertEqual(self.client.get('/team/files?folder=Archive%2F2026').json()['total'], 0)
        self.connection.execute("INSERT INTO data_folder(company_id,team_id,owner_id,path) SELECT %s,%s,%s,'Paged/' || lpad(number::text,2,'0') FROM generate_series(1,12) AS number", (self.company_id, self.team_id, fixture['account_id']))
        first = self.client.get('/team/files?folder=Paged').json()
        second = self.client.get('/team/files?folder=Paged&page=2').json()
        self.assertEqual(first['total'], 12)
        self.assertEqual(len(first['items']), 10)
        self.assertEqual([row['name'] for row in second['items']], ['11', '12'])
        self.assertFalse({row['id'] for row in first['items']} & {row['id'] for row in second['items']})
        self.client.cookies.delete('mikan_team_session')
        self.assertEqual(self.client.get('/team/files?folder=').status_code, 401)

    def test_data_administration_folders(self):
        fixture = self.private_file_fixture()
        base = '/company/teams/data'
        folder = {'team_id': self.team_id, 'owner_id': fixture['account_id'], 'path': 'Reports', 'action': 'create'}
        self.assertEqual(self.client.post(base + '/folders', json=folder).status_code, 200)
        self.assertEqual(self.client.post(base + '/folders', json=folder).status_code, 409)
        self.connection.execute('UPDATE stored_file SET folder=%s WHERE id=%s', ('Reports/2026',fixture['id']))
        self.assertEqual(self.client.get(base + '/folders').json()['total'], 2)
        self.assertEqual(self.client.post(base + '/folders', json={**folder, 'action': 'remove'}).status_code, 409)
        self.assertEqual(self.client.post(base + '/folders', json={**folder, 'action': 'rename', 'target': 'Reports/child'}).status_code, 422)
        self.connection.execute("UPDATE stored_file SET state='pending' WHERE id=%s", (fixture['id'],))
        rename = {**folder, 'action': 'rename', 'target': 'Archive'}
        self.assertEqual(self.client.post(base + '/folders', json=rename).status_code, 409)
        self.connection.execute("UPDATE stored_file SET state='ready' WHERE id=%s", (fixture['id'],))
        self.assertEqual(self.client.post(base + '/folders', json=rename).status_code, 200)
        self.assertEqual(self.connection.execute('SELECT folder FROM stored_file WHERE id=%s', (fixture['id'],)).fetchone()['folder'], 'Archive/2026')
        self.assertEqual(self.client.get(base + '/files?folder=Archive%2F2026').json()['total'], 1)
        empty = {**folder, 'path': 'Empty'}
        self.assertEqual(self.client.post(base + '/folders', json=empty).status_code, 200)
        self.assertEqual(self.client.post(base + '/folders', json={**empty, 'action': 'remove'}).status_code, 200)
        self.assertEqual(self.client.post(base + '/folders', json={**empty, 'path': '../invalid'}).status_code, 422)
        self.assertEqual(self.client.post(base + '/folders', json={**empty, 'owner_id': 2147483647}).status_code, 404)
        moved_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'Reassigned') RETURNING id", (self.company_id,)).fetchone()['id']
        self.connection.execute('UPDATE team_account SET team_id=%s WHERE id=%s', (moved_team,fixture['account_id']))
        self.assertEqual(self.client.post(base + '/folders', json={**folder, 'path': 'Archive', 'action': 'rename', 'target': 'Archived'}).status_code, 200)
        self.assertEqual(self.client.post(base + '/folders', json=empty).status_code, 404)
        self.client.cookies.clear()
        self.prepare_team()
        self.assertEqual(self.client.post(base + '/folders', json=rename).status_code, 404)

    @patch('uploads.storage_client')
    def test_multipart_content_identity(self, factory):
        fixture = self.private_file_fixture()
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id<>%s', (fixture['storage_id'],))
        self.assertEqual(self.client.post('/company/teams/workflows/uploads', json={'enabled': True, 'max_file_bytes': 100}).status_code, 200)
        identifier = self.client.post('/team/files/uploads', json={'name': 'drawing.dwg', 'size_bytes': 3, 'upload_key': str(uuid4())}).json()['id']
        path = f'/team/files/{identifier}'
        client = factory.return_value
        client.create_multipart_upload.return_value = {'UploadId': 'session'}
        client.list_parts.return_value = {'Parts': []}
        original = {'X-Upload-Fingerprint': 'a' * 64}
        changed = {'X-Upload-Fingerprint': 'b' * 64}
        self.assertEqual(self.client.post(path + '/multipart', json={}, headers=original).status_code, 200)
        factory.reset_mock()
        for suffix in ('/multipart', '/multipart/complete'):
            self.assertEqual(self.client.post(path + suffix, json={}, headers=changed).status_code, 409)
        self.assertEqual(self.client.post(path + '/parts/1', content=b'new', headers={**changed, 'Content-Type': 'application/octet-stream'}).status_code, 409)
        factory.assert_not_called()
        self.assertEqual(self.client.post(path + '/multipart', json={}, headers=original).status_code, 200)

    @patch('uploads.storage_client')
    def test_upload_rechecks_session_after_body(self, factory):
        from uploads import transfer_preflight
        fixture = self.private_file_fixture()
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id<>%s', (fixture['storage_id'],))
        self.client.post('/company/teams/workflows/uploads', json={'enabled': True, 'max_file_bytes': 100})
        identifier = self.client.post('/team/files/uploads', json={'name': 'drawing.dwg', 'size_bytes': 3, 'upload_key': str(uuid4())}).json()['id']

        def revoke_after_preflight(*args):
            limit = transfer_preflight(*args)
            self.connection.execute('DELETE FROM team_session WHERE account_id=%s', (fixture['account_id'],))
            return limit

        with patch('uploads.transfer_preflight', side_effect=revoke_after_preflight):
            response = self.client.post(f'/team/files/{identifier}/upload', content=b'abc', headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(response.status_code, 401, response.text)
        factory.assert_not_called()
        self.assertEqual(self.connection.execute('SELECT state FROM stored_file WHERE id=%s', (identifier,)).fetchone()['state'], 'pending')

    def test_database_closes_before_streaming(self):
        from fastapi import Depends, FastAPI
        from fastapi.responses import StreamingResponse
        probe = FastAPI()
        events = []

        def dependency():
            events.append('open')
            yield
            events.append('closed')

        def chunks():
            self.assertEqual(events, ['open', 'closed'])
            yield b'content'

        @probe.get('/')
        def download(connection=Depends(get_db, scope='function')):
            return StreamingResponse(chunks())

        probe.dependency_overrides[get_db] = dependency
        self.assertEqual(TestClient(probe).get('/').content, b'content')
        for route in app.routes:
            dependant = getattr(route, 'dependant', None)
            if dependant is None:
                continue
            pending = [dependant]
            while pending:
                dependency = pending.pop()
                if dependency.call is get_db:
                    self.assertEqual(dependency.scope, 'function', route.path)
                pending.extend(dependency.dependencies)

    @patch('uploads.storage_client')
    def test_upload_cancellation(self, factory):
        from botocore.exceptions import ClientError
        fixture = self.private_file_fixture()
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id<>%s', (fixture['storage_id'],))
        self.client.post('/company/teams/workflows/uploads', json={'enabled': True, 'max_file_bytes': 100})
        identifier = self.client.post('/team/files/uploads', json={'name': 'drawing.dwg', 'size_bytes': 3, 'upload_key': str(uuid4())}).json()['id']
        path = f'/team/files/{identifier}'
        client = factory.return_value
        client.list_multipart_uploads.return_value = {'Uploads': []}
        client.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        client.delete_object.side_effect = ClientError({'Error': {'Code': 'AccessDenied'}}, 'DeleteObject')
        self.connection.execute('UPDATE company_upload_policy SET enabled=FALSE WHERE company_id=%s', (self.company_id,))
        self.assertEqual(self.client.post(path + '/cancel', json={}).status_code, 503)
        self.assertEqual(self.connection.execute('SELECT state FROM stored_file WHERE id=%s', (identifier,)).fetchone()['state'], 'cancelling')
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 10)
        client.delete_object.side_effect = None
        self.assertEqual(self.client.post(path + '/cancel', json={}).status_code, 200)
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 7)
        factory.reset_mock()
        self.assertEqual(self.client.post(path + '/cancel', json={}).status_code, 200)
        factory.assert_not_called()
        self.assertEqual(self.client.post(f"/team/files/{fixture['id']}/cancel", json={}).status_code, 404)
        self.assertEqual(self.client.post(path + '/upload', content=b'abc', headers={'Content-Type': 'application/octet-stream'}).status_code, 409)
        self.assertNotIn(identifier, [str(item['id']) for item in self.client.get('/team/files').json()['items']])
        self.assertNotIn(identifier, [str(item['id']) for item in self.client.get('/company/teams/data/files').json()['items']])

    @patch('uploads.storage_client')
    def test_stale_revision_cancellation(self, factory):
        from botocore.exceptions import ClientError
        fixture = self.private_file_fixture()
        base = f"/team/files/{fixture['id']}"
        self.connection.execute('INSERT INTO company_upload_policy(company_id,connection_id,enabled,max_file_bytes) VALUES (%s,%s,true,100)', (self.company_id, fixture['storage_id']))
        payload = {'name': 'report.html', 'size_bytes': 3, 'base_version': 1, 'upload_key': str(uuid4())}
        stale = self.client.post(base + '/versions', json=payload).json()['id']
        newer = self.client.post(base + '/versions', json={**payload, 'upload_key': str(uuid4())}).json()['id']
        client = factory.return_value
        client.put_object.return_value = {'ETag': 'new'}
        client.head_object.return_value = {'ETag': 'new', 'ContentLength': 3}
        self.assertEqual(self.client.post(f'/team/files/{newer}/upload', content=b'new', headers={'Content-Type': 'application/octet-stream'}).status_code, 200)
        self.assertEqual(self.client.post(f'/team/files/{stale}/multipart', json={}).status_code, 409)
        client.list_multipart_uploads.return_value = {'Uploads': []}
        client.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        self.assertEqual(self.client.post(f'/team/files/{stale}/cancel', json={}).status_code, 200)
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 10)
        self.assertEqual(self.connection.execute('SELECT state,quota_bytes FROM file_version WHERE id=%s', (stale,)).fetchone(), {'state': 'cancelled', 'quota_bytes': 0})
        self.assertEqual(self.client.post(f'/team/files/{newer}/cancel', json={}).status_code, 409)
        self.assertEqual(self.client.get(base + '/versions').json()['current_version'], 3)
        self.private_file_fixture()
        self.assertEqual(self.client.post(f'/team/files/{stale}/cancel', json={}).status_code, 404)

    def test_cleanup_versioned_objects(self):
        from botocore.exceptions import ClientError
        from uploads import cleanup_upload
        client = Mock()
        client.list_multipart_uploads.side_effect = [{'Uploads': [{'Key': 'reserved', 'UploadId': 'orphan'}, {'Key': 'reserved-other', 'UploadId': 'keep'}]}, {'Uploads': []}]
        client.list_object_versions.side_effect = [{'Versions': [{'Key': 'reserved', 'VersionId': 'old'}, {'Key': 'reserved-other', 'VersionId': 'keep'}], 'DeleteMarkers': [{'Key': 'reserved', 'VersionId': 'marker'}]}, {}]
        client.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        cleanup_upload(client, {'bucket': 'private', 'provider': 's3'}, {'object_key': 'reserved', 'multipart_upload_id': None})
        client.abort_multipart_upload.assert_called_once_with(Bucket='private', Key='reserved', UploadId='orphan')
        self.assertEqual([call.kwargs['VersionId'] for call in client.delete_object.call_args_list], ['old', 'marker'])

    @patch('uploads.storage_client')
    def test_upload_operation_lock(self, factory):
        fixture = self.private_file_fixture()
        with connect() as other:
            other.autocommit = True
            other.execute('SELECT pg_advisory_lock(hashtextextended(%s,0))', (f"upload-operation:{fixture['id']}",))
            self.assertEqual(self.client.post(f"/team/files/{fixture['id']}/cancel", json={}).status_code, 409)
        factory.assert_not_called()

    def test_signed_authentication_client(self):
        import hashlib
        import hmac
        import time
        from starlette.requests import Request
        from teams import authentication_client, limited
        secret = 'test-proxy-secret-' * 3
        path = '/auth/team/password'

        def request(address, timestamp=None, signature=None):
            timestamp = str(int(time.time()) if timestamp is None else timestamp)
            signature = signature or hmac.new(secret.encode(), f'{timestamp}\nPOST\n{path}\n{address}'.encode(), hashlib.sha256).hexdigest()
            return Request({'type': 'http', 'method': 'POST', 'path': path, 'client': ('127.0.0.1', 3000),
                'headers': [(b'x-mikan-client-ip', address.encode()), (b'x-mikan-client-time', timestamp.encode()), (b'x-mikan-client-signature', signature.encode())]})

        with patch.dict('os.environ', {'TEAM_PROXY_SECRET': secret}):
            first = authentication_client(request('192.0.2.1'))
            second = authentication_client(request('192.0.2.2'))
            self.assertNotEqual(first, second)
            self.assertFalse(limited(self.connection, 'test-ip:' + first, 1, 900))
            self.assertTrue(limited(self.connection, 'test-ip:' + first, 1, 900))
            self.assertFalse(limited(self.connection, 'test-ip:' + second, 1, 900))
            for invalid in (request('192.0.2.1', signature='0' * 64), request('192.0.2.1', timestamp=1), request('not-an-ip')):
                with self.assertRaises(HTTPException) as error:
                    authentication_client(invalid)
                self.assertEqual(error.exception.status_code, 403)
        for configured_secret in ('', secret, 'short'):
            with self.subTest(configured_secret=configured_secret), patch.dict('os.environ', {'TEAM_PROXY_SECRET': configured_secret}):
                direct = Request({'type': 'http', 'method': 'POST', 'path': path, 'client': ('127.0.0.1', 3000),
                    'headers': [(b'x-forwarded-for', b'192.0.2.1'), (b'x-mikan-client-ip', b'192.0.2.2')]})
                self.assertEqual(authentication_client(direct), '127.0.0.1')

    def test_multipart_part_manifest(self):
        from uploads import MIN_PART_BYTES, multipart_parts
        client = Mock()
        file = {'object_key': 'private', 'multipart_upload_id': 'session', 'multipart_part_bytes': MIN_PART_BYTES, 'size_bytes': MIN_PART_BYTES + 3}
        first = {'PartNumber': 1, 'Size': MIN_PART_BYTES, 'ETag': 'first'}
        second = {'PartNumber': 2, 'Size': 3, 'ETag': 'second'}
        client.list_parts.side_effect = [{'Parts': [first], 'IsTruncated': True, 'NextPartNumberMarker': 1}, {'Parts': [second]}]
        self.assertEqual(multipart_parts(client, {'bucket': 'private'}, file), [first, second])
        self.assertEqual(client.list_parts.call_args.kwargs['PartNumberMarker'], 1)
        client.list_parts.side_effect = None
        for result in ({'Parts': [{**second, 'Size': 4}]}, {'Parts': [first, first]}, {'Parts': [], 'IsTruncated': True, 'NextPartNumberMarker': 0}, {'Parts': [{**second, 'PartNumber': 10001}]}):
            client.list_parts.return_value = result
            with self.assertRaises(ValueError):
                multipart_parts(client, {'bucket': 'private'}, file)

    @patch('uploads.storage_client')
    def test_multipart_limits(self, factory):
        from uploads import MULTIPART_UPLOAD_BYTES, SINGLE_UPLOAD_BYTES, multipart_part_bytes
        fixture = self.private_file_fixture()
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id<>%s', (fixture['storage_id'],))
        self.connection.execute('UPDATE team SET storage_quota_bytes=%s WHERE id=%s', (MULTIPART_UPLOAD_BYTES + 7, self.team_id))
        policy = {'enabled': True, 'max_file_bytes': MULTIPART_UPLOAD_BYTES}
        path = '/company/teams/workflows/uploads'
        self.assertEqual(self.client.post(path, json=policy).status_code, 200)
        self.assertEqual(self.client.post(path, json={**policy, 'max_file_bytes': MULTIPART_UPLOAD_BYTES + 1}).status_code, 422)
        payload = {'name': 'large.bin', 'size_bytes': MULTIPART_UPLOAD_BYTES, 'upload_key': str(uuid4())}
        response = self.client.post('/team/files/uploads', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        identifier = response.json()['id']
        self.assertEqual(self.client.post('/team/files/uploads', json={**payload, 'size_bytes': MULTIPART_UPLOAD_BYTES + 1, 'upload_key': str(uuid4())}).status_code, 422)
        self.assertEqual(self.client.post('/team/files/uploads', json={**payload, 'size_bytes': 1, 'upload_key': str(uuid4())}).status_code, 409)
        self.assertEqual(self.client.post(f'/team/files/{identifier}/upload', content=b'', headers={'Content-Type': 'application/octet-stream'}).status_code, 413)
        factory.assert_not_called()
        for size in (SINGLE_UPLOAD_BYTES, SINGLE_UPLOAD_BYTES + 1, MULTIPART_UPLOAD_BYTES):
            part = multipart_part_bytes(size)
            self.assertLessEqual((size + part - 1) // part, 10000)
            self.assertLessEqual(part, SINGLE_UPLOAD_BYTES)
        client = factory.return_value
        client.create_multipart_upload.return_value = {'UploadId': 'private-session'}
        response = self.client.post(f'/team/files/{identifier}/multipart', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['part_bytes'], multipart_part_bytes(MULTIPART_UPLOAD_BYTES))
        self.assertNotIn('private-session', response.text)

    @patch('uploads.storage_client')
    def test_multipart_resume_and_completion(self, factory):
        from uploads import MIN_PART_BYTES
        fixture = self.private_file_fixture()
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id<>%s', (fixture['storage_id'],))
        size = MIN_PART_BYTES + 3
        self.assertEqual(self.client.post('/company/teams/workflows/uploads', json={'enabled': True, 'max_file_bytes': size}).status_code, 200)
        identifier = self.client.post('/team/files/uploads', json={'name': 'parts.bin', 'size_bytes': size, 'upload_key': str(uuid4())}).json()['id']
        path = f'/team/files/{identifier}'
        client = factory.return_value
        client.create_multipart_upload.return_value = {'UploadId': 'private-session'}
        client.upload_part.return_value = {'ETag': 'part-etag'}
        client.list_parts.return_value = {'Parts': []}
        started = self.client.post(path + '/multipart', json={})
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()['part_bytes'], MIN_PART_BYTES)
        self.assertNotIn('private-session', started.text)
        binary = {'Content-Type': 'application/octet-stream'}
        self.assertEqual(self.client.post(path + '/parts/0', content=b'abc', headers=binary).status_code, 422)
        self.assertEqual(self.client.post(path + '/parts/10001', content=b'abc', headers=binary).status_code, 422)
        self.assertEqual(self.client.post(path + '/parts/3', content=b'abc', headers=binary).status_code, 422)
        self.assertEqual(self.client.post(path + '/parts/2', content=b'abcd', headers=binary).status_code, 413)
        self.assertEqual(self.client.post(path + '/parts/2', content=b'ab', headers=binary).status_code, 409)
        self.assertEqual(self.client.post(path + '/parts/2', content=b'abc', headers=binary).status_code, 200)
        self.assertEqual(self.client.post(path + '/parts/1', content=b'x' * MIN_PART_BYTES, headers=binary).status_code, 200)
        client.list_parts.return_value = {'Parts': [{'PartNumber': 1, 'Size': MIN_PART_BYTES, 'ETag': 'first'}]}
        self.assertEqual(self.client.post(path + '/multipart', json={}).json()['uploaded_parts'], [1])
        self.assertEqual(client.create_multipart_upload.call_count, 1)
        self.assertEqual(self.client.post(path + '/multipart/complete', json={}).status_code, 409)
        client.complete_multipart_upload.assert_not_called()
        client.list_parts.return_value['Parts'].append({'PartNumber': 2, 'Size': 3, 'ETag': 'second'})
        client.complete_multipart_upload.return_value = {'ETag': 'complete', 'VersionId': 'ignored-r2'}
        client.head_object.return_value = {'ETag': 'complete', 'ContentLength': size, 'Metadata': {'mikan-upload': identifier}}
        completed = self.client.post(path + '/multipart/complete', json={})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertNotIn('VersionId', client.head_object.call_args.kwargs)
        self.assertEqual(client.complete_multipart_upload.call_args.kwargs['MultipartUpload']['Parts'], [{'PartNumber': 1, 'ETag': 'first'}, {'PartNumber': 2, 'ETag': 'second'}])
        self.assertEqual(self.client.post(path + '/multipart/complete', json={}).status_code, 200)
        self.assertEqual(self.client.post(path + '/multipart', json={}).json()['state'], 'ready')
        self.assertEqual(client.complete_multipart_upload.call_count, 1)
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], size + 7)

    @patch('uploads.storage_client')
    def test_multipart_recovery_and_access(self, factory):
        from botocore.exceptions import ClientError
        fixture = self.private_file_fixture(provider='s3')
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id<>%s', (fixture['storage_id'],))
        self.assertEqual(self.client.post('/company/teams/workflows/uploads', json={'enabled': True, 'max_file_bytes': 100}).status_code, 200)
        identifier = self.client.post('/team/files/uploads', json={'name': 'parts.bin', 'size_bytes': 3, 'upload_key': str(uuid4())}).json()['id']
        path = f'/team/files/{identifier}'
        binary = {'Content-Type': 'application/octet-stream'}
        for suffix in ('/multipart', '/multipart/complete', '/parts/1'):
            self.assertEqual(self.client.post(path + suffix, content=b'abc', headers={**binary, 'Origin': 'https://untrusted.invalid'}).status_code, 403)
        client = factory.return_value
        client.create_multipart_upload.return_value = {'UploadId': 'session'}
        self.assertEqual(self.client.post(path + '/multipart', json={}).status_code, 200)
        client.list_parts.side_effect = ClientError({'Error': {'Code': 'NoSuchUpload'}}, 'ListParts')
        client.head_object.side_effect = ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        self.assertEqual(self.client.post(path + '/multipart/complete', json={}).status_code, 409)
        self.assertEqual(self.client.post(path + '/multipart', json={}).status_code, 200)
        self.assertEqual(client.create_multipart_upload.call_count, 2)
        client.list_parts.side_effect = None
        client.head_object.side_effect = None
        client.list_parts.return_value = {'Parts': [{'PartNumber': 1, 'Size': 3, 'ETag': 'part'}]}
        client.complete_multipart_upload.return_value = {'ETag': 'complete', 'VersionId': 's3-version'}
        client.head_object.return_value = {'ETag': 'complete', 'ContentLength': 3, 'Metadata': {'mikan-upload': 'wrong-file'}}
        self.assertEqual(self.client.post(path + '/multipart/complete', json={}).status_code, 503)
        self.assertEqual(client.head_object.call_args.kwargs['VersionId'], 's3-version')
        self.assertEqual(self.connection.execute('SELECT state FROM stored_file WHERE id=%s', (identifier,)).fetchone()['state'], 'pending')
        client.list_parts.side_effect = ClientError({'Error': {'Code': 'NoSuchUpload'}}, 'ListParts')
        client.head_object.return_value = {'ETag': 'complete', 'ContentLength': 3, 'Metadata': {'mikan-upload': identifier}, 'VersionId': 's3-version'}
        resumed = self.client.post(path + '/multipart', json={})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()['state'], 'ready')
        self.assertEqual(self.connection.execute('SELECT object_version FROM stored_file WHERE id=%s', (identifier,)).fetchone()['object_version'], 's3-version')
        factory.reset_mock()
        self.connection.execute('UPDATE company_upload_policy SET enabled=FALSE WHERE company_id=%s', (self.company_id,))
        self.assertEqual(self.client.post(path + '/multipart', json={}).status_code, 409)
        self.assertEqual(self.client.post(path + '/parts/1', content=b'abc', headers=binary).status_code, 409)
        self.client.cookies.delete('mikan_team_session')
        for suffix in ('/multipart', '/multipart/complete', '/parts/1'):
            self.assertEqual(self.client.post(path + suffix, content=b'abc', headers=binary).status_code, 401)
        factory.assert_not_called()

    @patch('uploads.storage_client')
    def test_automation_upload_reservation_and_trigger(self, factory):
        from automation import process_one
        fixture = self.private_file_fixture()
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id<>%s', (fixture['storage_id'],))
        policy = {'enabled': True, 'max_file_bytes': 100}
        self.assertEqual(self.client.post('/company/teams/workflows/uploads', json=policy).status_code, 200)
        self.assertEqual(self.client.get('/company/teams/workflows/uploads').json(), {'policy': policy})
        self.assertEqual(self.client.post('/company/teams/workflows/uploads', json={**policy, 'connection_id': fixture['storage_id']}).status_code, 422)
        graph = {'nodes': [
            {'id': 'start', 'type': 'trigger', 'position': {'x': 0, 'y': 0}, 'data': {'label': 'Uploaded', 'trigger': 'upload', 'file_suffix': '.pdf'}},
            {'id': 'move', 'type': 'file', 'position': {'x': 0, 'y': 100}, 'data': {'label': 'Move', 'file_action': 'move', 'target': 'Processed'}},
            {'id': 'end', 'type': 'end', 'position': {'x': 0, 'y': 200}, 'data': {'label': 'Done'}}],
            'edges': [{'id': 'first', 'source': 'start', 'target': 'move', 'sourceHandle': 'next'}, {'id': 'second', 'source': 'move', 'target': 'end', 'sourceHandle': 'next'}]}
        flow = self.client.post('/company/teams/workflows', json={'name': 'On upload', 'team_ids': [self.team_id], 'submitter_roles': ['member'], 'enabled': True, 'graph': graph})
        self.assertEqual(flow.status_code, 201, flow.text)
        payload = {'name': 'invoice.pdf', 'folder': 'Incoming', 'size_bytes': 4, 'upload_key': str(uuid4())}
        reserved = self.client.post('/team/files/uploads', json=payload)
        self.assertEqual(reserved.status_code, 201, reserved.text)
        identifier = reserved.json()['id']
        self.assertEqual(self.client.get('/team/files?folder=Incoming').json()['items'][0]['id'], identifier)
        self.assertIn('Incoming', [row.get('path') for row in self.client.get('/team/files?folder=').json()['items']])
        self.assertEqual(self.client.post('/team/files/uploads', json=payload).json()['id'], identifier)
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], 11)
        client = factory.return_value
        client.put_object.return_value = {'ETag': 'new-etag', 'VersionId': 'ignored-r2'}
        client.head_object.return_value = {'ETag': 'new-etag', 'ContentLength': 4}
        path = f'/team/files/{identifier}/upload'
        self.assertEqual(self.client.post(path, content=b'123', headers={'Content-Type': 'application/octet-stream'}).status_code, 409)
        factory.assert_not_called()
        client.head_object.return_value['ContentLength'] = 5
        failed = self.client.post(path, content=b'1234', headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(failed.status_code, 503, failed.text)
        self.assertEqual(self.connection.execute('SELECT state FROM stored_file WHERE id=%s', (identifier,)).fetchone()['state'], 'pending')
        client.head_object.return_value['ContentLength'] = 4
        completed = self.client.post(path, content=b'1234', headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertNotIn('VersionId', client.head_object.call_args.kwargs)
        self.assertEqual(self.client.post(path, content=b'1234', headers={'Content-Type': 'application/octet-stream'}).status_code, 200)
        self.assertEqual(client.put_object.call_count, 2)
        self.assertEqual(self.connection.execute('SELECT count(*) AS total FROM automation_job WHERE file_id=%s', (identifier,)).fetchone()['total'], 1)
        self.assertTrue(process_one(self.connection))
        job = self.connection.execute('SELECT * FROM automation_job WHERE file_id=%s', (identifier,)).fetchone()
        self.assertEqual(job['status'], 'done', job['last_error'])
        self.assertEqual(self.connection.execute('SELECT folder FROM stored_file WHERE id=%s', (identifier,)).fetchone()['folder'], 'Processed')
        self.assertEqual(self.client.get('/team/files?folder=Incoming').status_code, 404)
        self.assertEqual(self.client.get('/team/files?folder=Processed').json()['items'][0]['id'], identifier)
        self.assertIn('Processed', [row.get('path') for row in self.client.get('/team/files?folder=').json()['items']])
        listed = self.client.get('/team/files')
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertNotIn('object_key', listed.text)
        self.assertEqual(self.client.get('/company/teams/workflows/jobs').status_code, 200)
        self.assertEqual(self.client.post('/team/files/uploads', json={**payload, 'upload_key': str(uuid4()), 'size_bytes': 101}).status_code, 413)
        self.connection.execute('UPDATE team SET storage_quota_bytes=storage_used_bytes WHERE id=%s', (self.team_id,))
        self.assertEqual(self.client.post('/team/files/uploads', json={**payload, 'upload_key': str(uuid4())}).status_code, 409)

    @patch('uploads.storage_client')
    def test_automation_upload_access_boundaries(self, factory):
        fixture = self.private_file_fixture()
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id<>%s', (fixture['storage_id'],))
        self.assertEqual(self.client.post('/company/teams/workflows/uploads', json={'enabled': True, 'max_file_bytes': 100}).status_code, 200)
        payload = {'name': 'private.pdf', 'size_bytes': 4, 'upload_key': str(uuid4())}
        identifier = self.client.post('/team/files/uploads', json=payload).json()['id']
        path = f'/team/files/{identifier}/upload'
        self.assertEqual(self.client.post(path, content=b'1234', headers={'Content-Type': 'application/octet-stream', 'Origin': 'https://untrusted.invalid'}).status_code, 403)
        self.assertEqual(self.client.post(path, content=b'12345', headers={'Content-Type': 'application/octet-stream'}).status_code, 413)
        self.assertEqual(self.client.post('/team/files/uploads', json={**payload, 'name': 'changed.pdf'}).status_code, 409)
        self.assertEqual(self.client.post('/team/files/uploads', json={**payload, 'folder': '../another-owner'}).status_code, 422)
        company = self.client.post('/companies/', json=self.company_payload()).json()['id']
        foreign_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'Other') RETURNING id", (company,)).fetchone()['id']
        for company_id, team_id in ((self.company_id, self.team_id), (company, foreign_team)):
            owner = self.connection.execute("INSERT INTO team_account(company_id,team_id,name,email,mobile,role,status,auth_type) VALUES (%s,%s,'Other',%s,'9876543210','member','active','otp') RETURNING id", (company_id, team_id, f'{uuid4()}@example.com')).fetchone()['id']
            token = secrets.token_urlsafe(32)
            self.connection.execute("INSERT INTO team_session VALUES (%s,%s,clock_timestamp()+INTERVAL '1 hour')", (hash_token(token), owner))
            self.client.cookies.set('mikan_team_session', token)
            self.assertEqual(self.client.post(path, content=b'1234', headers={'Content-Type': 'application/octet-stream'}).status_code, 404)
            for suffix in ('multipart', 'multipart/complete', 'parts/1'):
                self.assertEqual(self.client.post(f'/team/files/{identifier}/{suffix}', content=b'1234', headers={'Content-Type': 'application/octet-stream'}).status_code, 404)
            self.assertNotIn(identifier, self.client.get('/team/files').text)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.connection.execute('UPDATE company_upload_policy SET enabled=FALSE WHERE company_id=%s', (self.company_id,))
        self.assertEqual(self.client.post(path, content=b'1234', headers={'Content-Type': 'application/octet-stream'}).status_code, 409)
        factory.assert_not_called()

    def test_automation_upload_policy_hides_storage(self):
        fixture = self.private_file_fixture()
        path = '/company/teams/workflows/uploads'
        self.assertEqual(self.client.get(path).json(), {'policy': None})
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE')
        policy = {'enabled': True, 'max_file_bytes': 100}
        self.assertEqual(self.client.post(path, json=policy).status_code, 409)
        self.connection.execute('UPDATE storage_connection SET enabled=TRUE WHERE id=%s', (fixture['storage_id'],))
        self.assertEqual(self.client.post(path, json=policy).status_code, 200)
        self.connection.execute('UPDATE storage_connection SET enabled=TRUE')
        self.assertEqual(self.client.post(path, json={**policy, 'max_file_bytes': 200}).status_code, 200)
        self.assertEqual(self.connection.execute('SELECT connection_id FROM company_upload_policy WHERE company_id=%s', (self.company_id,)).fetchone()['connection_id'], fixture['storage_id'])
        self.assertEqual(self.client.get(path).json(), {'policy': {**policy, 'max_file_bytes': 200}})
        self.connection.execute('UPDATE storage_connection SET enabled=FALSE WHERE id=%s', (fixture['storage_id'],))
        self.assertEqual(self.client.post(path, json=policy).status_code, 409)
        self.assertEqual(self.client.post(path, json={**policy, 'enabled': False}).status_code, 200)

    @patch('automation.send_email')
    def test_automation_email_pause(self, deliver):
        from automation import enqueue_upload, process_one
        fixture = self.private_file_fixture()
        graph = {'nodes': [
            {'id': 'start', 'type': 'trigger', 'position': {'x': 0, 'y': 0}, 'data': {'label': 'Upload', 'trigger': 'upload'}},
            {'id': 'notice', 'type': 'notify', 'position': {'x': 0, 'y': 100}, 'data': {'label': 'Email owner', 'channel': 'email', 'message': 'File received'}},
            {'id': 'end', 'type': 'end', 'position': {'x': 0, 'y': 200}, 'data': {'label': 'Done'}}],
            'edges': [{'id': 'first', 'source': 'start', 'target': 'notice', 'sourceHandle': 'next'}, {'id': 'second', 'source': 'notice', 'target': 'end', 'sourceHandle': 'next'}]}
        flow = self.client.post('/company/teams/workflows', json={'name': 'Email on upload', 'team_ids': [self.team_id], 'submitter_roles': ['member'], 'enabled': True, 'graph': graph})
        self.assertEqual(flow.status_code, 201, flow.text)
        owner = self.connection.execute('SELECT * FROM team_account WHERE id=%s', (fixture['account_id'],)).fetchone()
        enqueue_upload(self.connection, fixture['id'], owner)
        self.assertTrue(process_one(self.connection))
        run = self.connection.execute('SELECT * FROM workflow_run WHERE workflow_id=%s', (flow.json()['id'],)).fetchone()
        self.connection.execute('UPDATE workflow SET enabled=FALSE WHERE id=%s', (flow.json()['id'],))
        self.assertTrue(process_one(self.connection))
        deliver.assert_not_called()
        job = self.connection.execute("SELECT * FROM automation_job WHERE run_id=%s AND kind='email'", (run['id'],)).fetchone()
        self.assertEqual(job['status'], 'retry')
        self.connection.execute('UPDATE workflow SET enabled=TRUE WHERE id=%s', (flow.json()['id'],))
        self.assertEqual(self.client.post(f"/company/teams/workflows/jobs/{job['id']}/retry", json={}).status_code, 200)
        self.assertTrue(process_one(self.connection))
        deliver.assert_called_once()
        self.assertEqual(deliver.call_args.args[1], owner['email'])
        self.assertEqual(self.connection.execute('SELECT status FROM workflow_run WHERE id=%s', (run['id'],)).fetchone()['status'], 'approved')

    def test_automation_webhook_rejects_private_destinations(self):
        from automation import post_webhook
        for url, addresses in (('http://example.com/hook', []), ('https://example.com/hook', ['127.0.0.1']), ('https://example.com/hook', ['93.184.216.34', '10.0.0.1'])):
            with patch.dict('os.environ', {'MIKAN_WEBHOOKS': '{"123":{"audit":{"url":"' + url + '","secret":"' + 'x' * 32 + '"}}}'}), patch('automation.socket.getaddrinfo', return_value=[(2, 1, 6, '', (address, 443)) for address in addresses]), patch('automation.urllib3.HTTPSConnectionPool') as pool:
                with self.assertRaises(HTTPException):
                    post_webhook(123, 'audit', {'message': 'test'}, uuid4())
                pool.assert_not_called()

    @patch('automation.post_webhook')
    def test_automation_schedule_delivery_retry(self, deliver):
        from automation import enqueue_schedules, process_one
        fixture = self.private_file_fixture()
        graph = {'nodes': [
            {'id': 'start', 'type': 'trigger', 'position': {'x': 0, 'y': 0}, 'data': {'label': 'Scheduled', 'trigger': 'schedule', 'interval_minutes': 60}},
            {'id': 'notice', 'type': 'notify', 'position': {'x': 0, 'y': 100}, 'data': {'label': 'Send', 'channel': 'webhook', 'webhook_name': 'audit', 'message': 'Ready'}},
            {'id': 'end', 'type': 'end', 'position': {'x': 0, 'y': 200}, 'data': {'label': 'Done'}}],
            'edges': [{'id': 'first', 'source': 'start', 'target': 'notice', 'sourceHandle': 'next'}, {'id': 'second', 'source': 'notice', 'target': 'end', 'sourceHandle': 'next'}]}
        with patch.dict('os.environ', {'MIKAN_WEBHOOKS': '{"' + str(self.company_id) + '":{"audit":{"url":"https://example.com","secret":"' + 'x' * 32 + '"}}}'}):
            flow = self.client.post('/company/teams/workflows', json={'name': 'Scheduled delivery', 'team_ids': [self.team_id], 'submitter_roles': ['member'], 'enabled': True, 'graph': graph})
        self.assertEqual(flow.status_code, 201, flow.text)
        enqueue_schedules(self.connection)
        enqueue_schedules(self.connection)
        self.assertEqual(self.connection.execute("SELECT count(*) AS total FROM automation_job WHERE workflow_id=%s AND kind='trigger'", (flow.json()['id'],)).fetchone()['total'], 1)
        self.assertTrue(process_one(self.connection))
        run = self.connection.execute('SELECT * FROM workflow_run WHERE workflow_id=%s', (flow.json()['id'],)).fetchone()
        self.assertIsNotNone(run)
        self.assertEqual(run['current_node'], 'notice')
        deliver.side_effect = HTTPException(502, 'Webhook unavailable.')
        self.assertTrue(process_one(self.connection))
        job = self.connection.execute("SELECT * FROM automation_job WHERE run_id=%s AND kind='webhook'", (run['id'],)).fetchone()
        self.assertEqual(job['status'], 'retry')
        self.assertEqual(self.client.post(f"/company/teams/workflows/jobs/{job['id']}/retry", json={'comment': ''}).status_code, 200)
        deliver.side_effect = None
        self.assertTrue(process_one(self.connection))
        self.assertEqual(self.connection.execute('SELECT status FROM workflow_run WHERE id=%s', (run['id'],)).fetchone()['status'], 'approved')
        self.assertEqual(deliver.call_args.args[2]['file_id'], fixture['id'])
        self.assertNotIn('object_key', deliver.call_args.args[2])

    @patch("storage.boto3.client")
    def test_private_files_require_team_session(self, factory):
        path = "/team/files/00000000-0000-0000-0000-000000000001/content"
        response = self.client.get(path)
        self.assertEqual(response.status_code, 401)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.login()
        self.assertEqual(self.client.get(path).status_code, 401)
        factory.assert_not_called()

    def private_file_fixture(self, provider="r2"):
        self.prepare_team()
        self.assertEqual(self.client.post(f"/company/teams/folders/{self.team_id}", json={"storage_quota_bytes": 1000000000, "manager_can_view_drives": False}).status_code, 200)
        payload = {"name": "Private storage", "bucket": "private-" + secrets.token_hex(8),
                   "region": "auto" if provider == "r2" else "ap-south-1",
                   "account_id": "a" * 32 if provider == "r2" else None,
                   "access_key": "test-access", "secret_key": "test-secret", "enabled": True}
        created = self.client.post(f"/integrations/storage/{provider}", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        storage_id = created.json()["id"]
        account_id = self.connection.execute(
            """INSERT INTO team_account (company_id, team_id, name, email, mobile, role, status, auth_type)
               VALUES (%s, %s, 'Owner', %s, '9876543210', 'member', 'active', 'otp') RETURNING id""",
            (self.company_id, self.team_id, f"owner-{secrets.token_hex(8)}@example.com"),
        ).fetchone()["id"]
        token = secrets.token_urlsafe(32)
        self.connection.execute("INSERT INTO team_session VALUES (%s, %s, clock_timestamp() + INTERVAL '1 hour')", (hash_token(token), account_id))
        self.client.cookies.set("mikan_team_session", token)
        file_id = self.connection.execute(
            """INSERT INTO stored_file (company_id, team_id, owner_id, connection_id, object_key,
               object_version, etag, name, size_bytes, state)
               VALUES (%s, %s, %s, %s, 'private/object', 'stored-version', '"test-etag"', 'report.html', 7, 'ready') RETURNING id""",
            (self.company_id, self.team_id, account_id, storage_id),
        ).fetchone()["id"]
        return {"path": f"/team/files/{file_id}/content", "id": file_id, "account_id": account_id,
                "token": token, "storage_id": storage_id, "payload": payload}

    @patch("files.boto3.client")
    def test_private_files_isolation_and_revocation(self, factory):
        fixture = self.private_file_fixture()
        self.connection.execute("INSERT INTO data_folder(company_id,team_id,owner_id,path) VALUES (%s,%s,%s,'Private folder')", (self.company_id, self.team_id, fixture['account_id']))
        path = fixture["path"]
        other_team = self.connection.execute("INSERT INTO team (company_id, name) VALUES (%s, 'Other') RETURNING id", (self.company_id,)).fetchone()["id"]
        other_company = self.client.post("/companies/", json=self.company_payload()).json()["id"]
        foreign_team = self.connection.execute("INSERT INTO team (company_id, name) VALUES (%s, 'Foreign') RETURNING id", (other_company,)).fetchone()["id"]
        for company_id, team_id, role in ((self.company_id, self.team_id, "member"),
                                         (self.company_id, self.team_id, "manager"),
                                         (self.company_id, other_team, "member"),
                                         (other_company, foreign_team, "member")):
            account_id = self.connection.execute(
                """INSERT INTO team_account (company_id, team_id, name, email, mobile, role, status, auth_type)
                   VALUES (%s, %s, 'Other', %s, '9876543210', %s, 'active', 'otp') RETURNING id""",
                (company_id, team_id, f"other-{secrets.token_hex(8)}@example.com", role),
            ).fetchone()["id"]
            token = secrets.token_urlsafe(32)
            self.connection.execute("INSERT INTO team_session VALUES (%s, %s, clock_timestamp() + INTERVAL '1 hour')", (hash_token(token), account_id))
            self.client.cookies.set("mikan_team_session", token)
            response = self.client.get(path + f"?owner_id={fixture['account_id']}&team_id={self.team_id}&company_id={self.company_id}")
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json(), {"detail": "File not found."})
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertEqual(self.client.get(path.replace('/content', '/preview')).status_code, 404)
            browse = self.client.get('/team/files', params={'folder': '', 'owner_id': fixture['account_id'], 'team_id': self.team_id, 'company_id': self.company_id})
            self.assertEqual(browse.status_code, 403, browse.text)
            self.assertNotIn('Private folder', browse.text)
            self.assertEqual(self.client.get('/team/files?folder=').json()['total'], 0)
            self.assertEqual(self.client.get('/team/files', params={'folder': 'Private folder'}).status_code, 404)
        self.client.cookies.set("mikan_team_session", fixture["token"])
        self.assertEqual(self.client.get("/team/files/00000000-0000-0000-0000-000000000001/content").status_code, 404)
        self.assertEqual(self.client.get("/team/files/not-a-uuid/content").status_code, 422)
        for state in ("pending", "quarantined", "trashed"):
            self.connection.execute("UPDATE stored_file SET state=%s WHERE id=%s", (state, fixture["id"]))
            self.assertEqual(self.client.get(path).status_code, 404)
        self.connection.execute("UPDATE stored_file SET state='ready' WHERE id=%s", (fixture["id"],))
        self.connection.execute("UPDATE storage_connection SET enabled=false WHERE id=%s", (fixture["storage_id"],))
        self.assertEqual(self.client.get(path).status_code, 404)
        self.connection.execute("UPDATE storage_connection SET enabled=true WHERE id=%s", (fixture["storage_id"],))
        self.connection.execute("UPDATE team_account SET team_id=%s WHERE id=%s", (other_team, fixture["account_id"]))
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/team/files?folder=').json()['total'], 0)
        self.assertEqual(self.client.get('/team/files', params={'folder': 'Private folder'}).status_code, 404)
        self.connection.execute("UPDATE team_account SET team_id=%s, status='disabled' WHERE id=%s", (self.team_id, fixture["account_id"]))
        self.assertEqual(self.client.get(path).status_code, 401)
        self.connection.execute("UPDATE team_account SET status='active' WHERE id=%s", (fixture["account_id"],))
        self.connection.execute("UPDATE team_session SET expires_at=clock_timestamp() - INTERVAL '1 second' WHERE token_hash=%s", (hash_token(fixture["token"]),))
        self.assertEqual(self.client.get(path).status_code, 401)
        self.connection.execute("DELETE FROM team_session WHERE token_hash=%s", (hash_token(fixture["token"]),))
        self.assertEqual(self.client.get(path).status_code, 401)
        self.client.cookies.delete("mikan_team_session")
        self.assertEqual(self.client.get(path).status_code, 401)
        factory.assert_not_called()

    @patch('uploads.storage_client')
    def test_restricted_file_versions(self, factory):
        fixture = self.private_file_fixture()
        base = f"/team/files/{fixture['id']}"
        self.connection.execute('INSERT INTO company_upload_policy(company_id,connection_id,enabled) VALUES (%s,%s,true)', (self.company_id, fixture['storage_id']))
        other_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'Editors') RETURNING id", (self.company_id,)).fetchone()['id']
        person = self.connection.execute("""INSERT INTO team_account(company_id,team_id,name,email,mobile,role,status,auth_type)
            VALUES (%s,%s,'Editor',%s,'9876543210','member','active','otp') RETURNING id""",
            (self.company_id, other_team, f'editor-{secrets.token_hex(8)}@example.com')).fetchone()['id']
        token = secrets.token_urlsafe(32)
        self.connection.execute("INSERT INTO team_session VALUES (%s,%s,clock_timestamp()+INTERVAL '1 hour')", (hash_token(token), person))
        original_link = self.client.get(base + '/link').json()['url']
        original = self.client.get(base + '/versions').json()['items'][0]
        baseline = self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes']
        payload = {'name': 'report.html', 'size_bytes': 11, 'upload_key': str(uuid4()), 'base_version': 1}
        self.assertEqual(self.client.post(base + '/shares', json={'recipient_ids': [person]}).status_code, 200)
        self.client.cookies.set('mikan_team_session', token)
        self.assertFalse(self.client.get(base + '/versions').json()['can_edit'])
        self.assertEqual(self.client.post(base + '/versions', json=payload).status_code, 403)
        permission_path = base + f'/shares/{person}/permission'
        self.assertEqual(self.client.post(permission_path, json={'permission': 'edit'}).status_code, 404)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.assertEqual(self.client.post(permission_path, json={'permission': 'invalid'}).status_code, 422)
        self.assertEqual(self.client.post(permission_path, json={'permission': 'edit'}).status_code, 200)
        self.client.cookies.set('mikan_team_session', token)
        self.assertTrue(self.client.get(base + '/versions').json()['can_edit'])
        response = self.client.post(base + '/versions', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        identifier = response.json()['id']
        self.assertEqual(self.client.post(base + '/versions', json=payload).json()['id'], identifier)
        stale = self.client.post(base + '/versions', json={**payload, 'upload_key': str(uuid4())}).json()['id']
        upload = f'/team/files/{identifier}/upload'
        factory.return_value.put_object.return_value = {'ETag': 'revision-etag'}
        factory.return_value.head_object.return_value = {'ContentLength': 11, 'ETag': 'revision-etag'}
        response = self.client.post(upload, content=b'new content', headers={'Content-Type': 'application/octet-stream'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post(upload, content=b'new content', headers={'Content-Type': 'application/octet-stream'}).status_code, 200)
        self.assertEqual(factory.return_value.put_object.call_count, 1)
        history = self.client.get(base + '/versions').json()
        self.assertEqual(history['current_version'], 2)
        self.assertEqual(history['total'], 3)
        self.assertEqual(self.client.post(f'/team/files/{stale}/upload', content=b'new content', headers={'Content-Type': 'application/octet-stream'}).status_code, 409)
        self.assertEqual(self.client.post(base + f"/versions/{original['id']}/restore", json={}).status_code, 403)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.assertEqual(self.client.get(base + '/link').json()['url'], original_link)
        for target in (original['id'], identifier, original['id'], original['id'], identifier):
            result = self.client.post(base + f'/versions/{target}/restore', json={})
            self.assertEqual(result.status_code, 200, result.text)
            used = self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes']
            self.assertEqual(used, baseline + 22)
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (other_team,)).fetchone()['storage_used_bytes'], 0)
        self.assertGreater(self.client.get(base + '/activity').json()['total'], 5)
        self.assertEqual(self.client.get(base + '/versions?from_date=2999-01-01').json()['total'], 0)
        with patch('files.boto3.client') as storage:
            storage.return_value.get_object.return_value = {'Body': io.BytesIO(b'content'), 'ContentLength': 7, 'ETag': '"test-etag"'}
            downloaded = self.client.get(base + f"/versions/{original['id']}/content")
            self.assertEqual(downloaded.content, b'content')
            self.assertIn('no-store', downloaded.headers['cache-control'])
        current = self.client.get(base + '/versions').json()['current_version']
        self.client.cookies.set('mikan_team_session', token)
        pending = self.client.post(base + '/versions', json={**payload, 'upload_key': str(uuid4()), 'base_version': current}).json()['id']
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.assertEqual(self.client.post(base + '/link', json={'public_access': True}).status_code, 200)
        self.assertEqual(self.client.get(base + '/shares').json()['items'][0]['permission'], 'view')
        self.assertEqual(self.client.post(permission_path, json={'permission': 'edit'}).status_code, 409)
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.post(f'/team/files/{pending}/multipart', json={}).status_code, 403)
        self.assertEqual(self.client.post(f'/team/files/{pending}/upload', content=b'new content', headers={'Content-Type': 'application/octet-stream'}).status_code, 403)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.client.post(base + '/link', json={'public_access': False})
        self.client.post(permission_path, json={'permission': 'edit'})
        self.client.post(base + f'/shares/{person}/remove', json={})
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.get(base + '/versions').status_code, 404)
        self.assertEqual(self.client.get(base + '/activity').status_code, 404)
        self.assertEqual(self.client.post(f'/team/files/{pending}/upload', content=b'new content', headers={'Content-Type': 'application/octet-stream'}).status_code, 404)
        self.client.cookies.delete('mikan_team_session')
        self.assertEqual(self.client.get(base + '/versions').status_code, 401)

    @patch('uploads.storage_client')
    def test_file_version_multipart_and_quota(self, factory):
        from uploads import MIN_PART_BYTES
        fixture = self.private_file_fixture()
        base = f"/team/files/{fixture['id']}"
        size = MIN_PART_BYTES + 3
        self.connection.execute('INSERT INTO company_upload_policy(company_id,connection_id,enabled,max_file_bytes) VALUES (%s,%s,true,%s)',
            (self.company_id, fixture['storage_id'], size))
        used = self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes']
        self.connection.execute('UPDATE team SET storage_quota_bytes=%s WHERE id=%s', (used + size, self.team_id))
        payload = {'name': 'report.html', 'size_bytes': size, 'base_version': 1, 'upload_key': str(uuid4())}
        self.assertEqual(self.client.post(base + '/versions', json={**payload, 'size_bytes': size + 1}).status_code, 413)
        self.assertEqual(self.client.post(base + '/versions', json=payload, headers={'Origin': 'https://evil.invalid'}).status_code, 403)
        response = self.client.post(base + '/versions', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        identifier = response.json()['id']
        self.assertEqual(self.client.post(base + '/versions', json={**payload, 'upload_key': str(uuid4())}).status_code, 409)
        client = factory.return_value
        client.create_multipart_upload.return_value = {'UploadId': 'revision-session'}
        client.list_parts.return_value = {'Parts': []}
        client.upload_part.return_value = {'ETag': 'part'}
        path = f'/team/files/{identifier}'
        started = self.client.post(path + '/multipart', json={})
        self.assertEqual(started.status_code, 200, started.text)
        self.assertNotIn('revision-session', started.text)
        binary = {'Content-Type': 'application/octet-stream'}
        self.assertEqual(self.client.post(path + '/parts/1', content=b'x' * MIN_PART_BYTES, headers=binary).status_code, 200)
        client.list_parts.return_value = {'Parts': [{'PartNumber': 1, 'Size': MIN_PART_BYTES, 'ETag': 'first'}]}
        self.assertEqual(self.client.post(path + '/multipart', json={}).json()['uploaded_parts'], [1])
        self.assertEqual(self.client.post(path + '/parts/2', content=b'abc', headers=binary).status_code, 200)
        client.list_parts.return_value['Parts'].append({'PartNumber': 2, 'Size': 3, 'ETag': 'second'})
        client.complete_multipart_upload.return_value = {'ETag': 'complete'}
        client.head_object.return_value = {'ContentLength': size, 'ETag': 'wrong', 'Metadata': {'mikan-upload': identifier}}
        self.assertEqual(self.client.post(path + '/multipart/complete', json={}).status_code, 503)
        self.assertEqual(self.client.get(base + '/versions').json()['current_version'], 1)
        self.client.post(base + '/link', json={'public_access': True})
        self.assertEqual(self.client.post(path + '/parts/2', content=b'abc', headers=binary).status_code, 403)
        self.assertEqual(self.client.post(path + '/multipart/complete', json={}).status_code, 403)
        self.client.post(base + '/link', json={'public_access': False})
        client.head_object.return_value['ETag'] = 'complete'
        completed = self.client.post(path + '/multipart/complete', json={})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(self.client.get(base + '/versions').json()['current_version'], 2)
        self.assertEqual(self.client.post(path + '/multipart', json={}).json()['state'], 'ready')
        self.assertEqual(self.connection.execute('SELECT storage_used_bytes FROM team WHERE id=%s', (self.team_id,)).fetchone()['storage_used_bytes'], used + size)
        self.assertEqual(self.connection.execute('SELECT count(*) AS total FROM automation_job WHERE file_id=%s', (fixture['id'],)).fetchone()['total'], 0)
        self.connection.execute('UPDATE storage_connection SET enabled=false WHERE id=%s', (fixture['storage_id'],))
        self.assertEqual(self.client.get(base + '/versions').status_code, 404)

    @patch('files.boto3.client')
    def test_file_share_link_access(self, factory):
        fixture = self.private_file_fixture()
        base = f"/team/files/{fixture['id']}"
        link = self.client.get(base + '/link').json()
        self.assertFalse(link['public_access'])
        token = link['url'].rsplit('/', 1)[1]
        self.assertRegex(token, r'^[0-9a-f]{64}$')
        path = '/team/files/link/' + token
        self.assertEqual(self.client.get(path).json()['name'], 'report.html')
        self.client.cookies.delete('mikan_team_session')
        for suffix in ('', '/content', '/preview'):
            response = self.client.get(path + suffix)
            self.assertEqual(response.status_code, 401)
            self.assertIn('no-store', response.headers['cache-control'])
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.assertEqual(self.client.post(base + '/link', json={'public_access': True}, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post(base + '/link', json={'public_access': 'true'}).status_code, 422)
        self.assertEqual(self.client.post(base + '/link', json={'public_access': True}).status_code, 200)
        self.client.cookies.delete('mikan_team_session')
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertNotIn('object_key', self.client.get(path).json())
        factory.return_value.get_object.return_value = {'Body': io.BytesIO(b'content'), 'ContentLength': 7, 'ETag': '"test-etag"'}
        downloaded = self.client.get(path + '/content')
        self.assertEqual(downloaded.content, b'content')
        self.assertTrue(downloaded.headers['content-disposition'].startswith('attachment;'))
        self.assertEqual(self.client.get(path + '/preview').status_code, 415)
        self.connection.execute("UPDATE stored_file SET name='shared.pdf' WHERE id=%s", (fixture['id'],))
        factory.return_value.get_object.return_value = {'Body': io.BytesIO(b'c'), 'ContentLength': 1, 'ETag': '"test-etag"', 'ContentRange': 'bytes 0-0/7'}
        self.assertEqual(self.client.get(path + '/preview', headers={'Range': 'bytes=0-0'}).status_code, 206)
        self.connection.execute('UPDATE storage_connection SET enabled=false WHERE id=%s', (fixture['storage_id'],))
        self.assertEqual(self.client.get(path).status_code, 404)
        self.connection.execute('UPDATE storage_connection SET enabled=true WHERE id=%s', (fixture['storage_id'],))
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.assertEqual(self.client.post(base + '/link', json={'public_access': False}).status_code, 200)
        self.assertEqual(self.client.get(base + '/link').json()['url'], link['url'])
        self.client.cookies.delete('mikan_team_session')
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get('/team/files/link/' + '0' * 64).status_code, 404)

    @patch('files.send_email')
    def test_file_share_email_queue(self, send):
        from files import process_share_email
        fixture = self.private_file_fixture()
        base = f"/team/files/{fixture['id']}"
        other_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'Share team') RETURNING id", (self.company_id,)).fetchone()['id']
        person = self.connection.execute("""INSERT INTO team_account(company_id,team_id,name,email,mobile,role,status,auth_type)
            VALUES (%s,%s,'Recipient',%s,'9876543210','member','active','otp') RETURNING id""",
            (self.company_id, other_team, f'share-{secrets.token_hex(8)}@example.com')).fetchone()['id']
        token = secrets.token_urlsafe(32)
        self.connection.execute("INSERT INTO team_session VALUES (%s,%s,clock_timestamp()+INTERVAL '1 hour')", (hash_token(token), person))
        path = '/team/files/link/' + self.client.get(base + '/link').json()['url'].rsplit('/', 1)[1]
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post(base + '/link', json={'public_access': True}).status_code, 404)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        for attempt in range(2):
            self.assertEqual(self.client.post(base + '/shares', json={'recipient_ids': [person]}).status_code, 200)
        self.assertEqual(self.client.get(base + '/shares').json()['items'][0]['email_status'], 'queued')
        with patch('files.email_settings', return_value={}):
            self.assertTrue(process_share_email(self.connection))
            self.assertFalse(process_share_email(self.connection))
        send.assert_called_once()
        self.assertIn('/share/', send.call_args.args[3])
        self.assertIn('Owner', send.call_args.args[3])
        self.assertIn('report.html', send.call_args.args[3])
        self.assertEqual(self.client.get(base + '/shares').json()['items'][0]['email_status'], 'accepted')
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.get(path).status_code, 200)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.client.post(base + f'/shares/{person}/remove')
        self.client.cookies.set('mikan_team_session', token)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.client.post(base + '/shares', json={'recipient_ids': [person]})
        with patch('files.email_settings', return_value={}):
            send.side_effect = HTTPException(502, 'Provider unavailable')
            for attempt in range(5):
                self.connection.execute('UPDATE file_share SET email_next_at=clock_timestamp() WHERE file_id=%s', (fixture['id'],))
                self.assertTrue(process_share_email(self.connection))
        self.assertEqual(self.client.get(base + '/shares').json()['items'][0]['email_status'], 'failed')
        self.assertEqual(self.client.post(base + f'/shares/{person}/retry-email').status_code, 200)
        self.connection.execute("UPDATE team_account SET status='disabled' WHERE id=%s", (person,))
        send.reset_mock()
        self.assertTrue(process_share_email(self.connection))
        send.assert_not_called()
        self.assertEqual(self.client.get(base + '/shares').json()['items'][0]['email_status'], 'cancelled')

    @patch("files.boto3.client")
    def test_private_file_company_sharing(self, factory):
        fixture = self.private_file_fixture()
        base = f"/team/files/{fixture['id']}"
        self.connection.execute("UPDATE stored_file SET name='shared.pdf' WHERE id=%s", (fixture['id'],))
        other_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'Design') RETURNING id", (self.company_id,)).fetchone()['id']
        foreign_company = self.client.post('/companies/', json=self.company_payload()).json()['id']
        foreign_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'Foreign') RETURNING id", (foreign_company,)).fetchone()['id']
        people = []
        for company_id, team_id, name, status in [(self.company_id, self.team_id, 'Local Person', 'active'),
                (self.company_id, other_team, 'Company Person', 'active'),
                (foreign_company, foreign_team, 'Foreign Person', 'active'),
                (self.company_id, other_team, 'Disabled Person', 'disabled'),
                (self.company_id, other_team, 'Invited Person', 'invited')]:
            email = f"share-{secrets.token_hex(8)}@example.com"
            person = self.connection.execute("""INSERT INTO team_account(company_id,team_id,name,email,mobile,role,status,auth_type)
                VALUES (%s,%s,%s,%s,'9876543210','member',%s,'otp') RETURNING id""", (company_id, team_id, name, email, status)).fetchone()['id']
            token = secrets.token_urlsafe(32)
            self.connection.execute("INSERT INTO team_session VALUES (%s,%s,clock_timestamp()+INTERVAL '1 hour')", (hash_token(token), person))
            people.append((person, token, email))
        choices = self.client.get(base + '/share-members').json()['items']
        self.assertEqual({person['id'] for person in choices}, {people[0][0], people[1][0]})
        for search in ('company person', people[1][2].upper()):
            response = self.client.get(base + '/share-members', params={'search': search})
            self.assertEqual([person['id'] for person in response.json()['items']], [people[1][0]])
            self.assertEqual(response.json()['items'][0]['team_name'], 'Design')
            self.assertIn('no-store', response.headers['cache-control'])
        for recipient in [people[2][0], people[3][0], people[4][0], fixture['account_id'], -1, 9223372036854775808]:
            self.assertEqual(self.client.post(base + '/shares', json={'recipient_ids': [people[0][0], recipient]}).status_code, 422)
        for payload in ({'recipient_ids': []}, {'recipient_ids': [str(people[0][0])]}, {'recipient_ids': [True]},
                        {'recipient_ids': [people[0][0]] * 51}, {'recipient_ids': [people[0][0]], 'company_id': foreign_company}):
            self.assertEqual(self.client.post(base + '/shares', json=payload).status_code, 422)
        self.assertEqual(self.client.get(base + '/shares').json()['total'], 0)
        self.assertEqual(self.client.post(base + '/shares', json={'recipient_ids': [people[1][0]]}, headers={'Origin': 'https://evil.example'}).status_code, 403)
        for state in ('pending', 'quarantined', 'trashed'):
            self.connection.execute('UPDATE stored_file SET state=%s WHERE id=%s', (state, fixture['id']))
            self.assertEqual(self.client.post(base + '/shares', json={'recipient_ids': [people[1][0]]}).status_code, 409)
        self.connection.execute("UPDATE stored_file SET state='ready' WHERE id=%s", (fixture['id'],))
        for attempt in range(2):
            response = self.client.post(base + '/shares', json={'recipient_ids': [people[1][0], people[1][0]]})
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(base + '/shares').json()['total'], 1)
        self.assertNotIn(people[1][0], [person['id'] for person in self.client.get(base + '/share-members').json()['items']])
        for person, token, email in people[:3]:
            self.client.cookies.set('mikan_team_session', token)
            for suffix in ('/share-members', '/shares'):
                self.assertEqual(self.client.get(base + suffix).status_code, 404)
            self.assertEqual(self.client.post(base + '/shares', json={'recipient_ids': [people[0][0]]}).status_code, 404)
            self.assertEqual(self.client.post(base + f'/shares/{people[1][0]}/remove').status_code, 404)
            listing = self.client.get('/team/files/shared').json()
            self.assertEqual(listing['total'], 1 if person == people[1][0] else 0)
            self.assertEqual(self.client.get('/team/files?folder=').json()['total'], 0)
            if person != people[1][0]:
                self.assertEqual(self.client.get(base + '/content').status_code, 404)
                self.assertEqual(self.client.get(base + '/preview').status_code, 404)
        factory.assert_not_called()
        self.client.cookies.set('mikan_team_session', people[1][1])
        listing = self.client.get('/team/files/shared?search=SHARED').json()
        self.assertEqual(listing['items'][0]['shared_by'], 'Owner')
        self.assertNotIn('object_key', listing['items'][0])
        self.assertEqual(self.client.get('/team/files/shared?search=missing').json()['total'], 0)
        self.assertEqual(self.client.get('/team/files/shared?page=2').json()['items'], [])
        self.assertEqual(self.client.get('/team/files/shared?from_date=2100-01-01').json()['total'], 0)
        self.assertEqual(self.client.get('/team/files/shared?from_date=2026-01-02&to_date=2026-01-01').status_code, 422)
        storage_client = factory.return_value
        for suffix in ('/content', '/preview'):
            storage_client.get_object.return_value = {'Body': io.BytesIO(b'content'), 'ContentLength': 7, 'ETag': '"test-etag"'}
            response = self.client.get(base + suffix)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, b'content')
        storage_client.get_object.return_value = {'Body': io.BytesIO(b'c'), 'ContentLength': 1, 'ETag': '"test-etag"', 'ContentRange': 'bytes 0-0/7'}
        self.assertEqual(self.client.get(base + '/preview', headers={'Range': 'bytes=0-0'}).status_code, 206)
        factory.reset_mock()
        for statement, values, restore, restore_values, expected in [
            ("UPDATE stored_file SET state='trashed' WHERE id=%s", (fixture['id'],), "UPDATE stored_file SET state='ready' WHERE id=%s", (fixture['id'],), 404),
            ('UPDATE storage_connection SET enabled=false WHERE id=%s', (fixture['storage_id'],), 'UPDATE storage_connection SET enabled=true WHERE id=%s', (fixture['storage_id'],), 404),
            ("UPDATE team_account SET status='disabled' WHERE id=%s", (fixture['account_id'],), "UPDATE team_account SET status='active' WHERE id=%s", (fixture['account_id'],), 404),
            ('UPDATE team_account SET team_id=%s WHERE id=%s', (other_team, fixture['account_id']), 'UPDATE team_account SET team_id=%s WHERE id=%s', (self.team_id, fixture['account_id']), 404),
            ("UPDATE team_account SET status='disabled' WHERE id=%s", (people[1][0],), "UPDATE team_account SET status='active' WHERE id=%s", (people[1][0],), 401)]:
            self.connection.execute(statement, values)
            self.assertEqual(self.client.get(base + '/content').status_code, expected)
            self.assertEqual(self.client.get(base + '/preview').status_code, expected)
            if expected == 404:
                self.assertEqual(self.client.get('/team/files/shared').json()['total'], 0)
            self.connection.execute(restore, restore_values)
        self.client.cookies.set('mikan_team_session', fixture['token'])
        self.assertEqual(self.client.post(base + f'/shares/{people[1][0]}/remove').status_code, 200)
        self.assertEqual(self.client.get(base + '/shares').json()['total'], 0)
        self.client.cookies.set('mikan_team_session', people[1][1])
        self.assertEqual(self.client.get(base + '/content').status_code, 404)
        self.assertEqual(self.client.get(base + '/preview').status_code, 404)
        self.assertEqual(self.client.get('/team/files/shared').json()['total'], 0)
        self.client.cookies.delete('mikan_team_session')
        for path in (base + '/share-members', base + '/shares', '/team/files/shared'):
            self.assertEqual(self.client.get(path).status_code, 401)
        factory.assert_not_called()

    @patch("files.boto3.client")
    def test_private_file_previews_and_ranges(self, factory):
        fixture = self.private_file_fixture()
        path = fixture["path"].replace("/content", "/preview")
        client = factory.return_value
        for name, media_type in (("photo.jpg", "image/jpeg"), ("clip.mp4", "video/mp4"), ("report.PDF", "application/pdf")):
            self.connection.execute("UPDATE stored_file SET name=%s WHERE id=%s", (name, fixture["id"]))
            client.get_object.return_value = {"Body": io.BytesIO(b"private"), "ContentLength": 7, "ETag": '"test-etag"'}
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"private")
            self.assertEqual(response.headers["content-type"], media_type)
            self.assertTrue(response.headers["content-disposition"].startswith("inline;"))
            self.assertEqual(response.headers["accept-ranges"], "bytes")
            self.assertIn("frame-ancestors 'self'", response.headers["content-security-policy"])
            self.assertIn("default-src 'none'", response.headers["content-security-policy"])
            self.assertIn("no-store", response.headers["cache-control"])
        for header, start, end in (("bytes=0-2", 0, 2), ("bytes=3-", 3, 6), ("bytes=-2", 5, 6), ("bytes=2-99", 2, 6)):
            content_range = f"bytes {start}-{end}/7"
            body = io.BytesIO(b"private"[start:end + 1])
            client.get_object.return_value = {"Body": body, "ContentLength": end - start + 1, "ETag": '"test-etag"', "ContentRange": content_range}
            response = self.client.get(path, headers={"Range": header})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b"private"[start:end + 1])
            self.assertEqual(response.headers["content-range"], content_range)
            self.assertEqual(client.get_object.call_args.kwargs["Range"], f"bytes={start}-{end}")
            self.assertNotIn("VersionId", client.get_object.call_args.kwargs)
            self.assertTrue(body.closed)
        factory.reset_mock()
        for header in ("bytes=7-", "bytes=4-2", "bytes=-0", "bytes=-", "bytes=0-1,3-4", "bad"):
            response = self.client.get(path, headers={"Range": header})
            self.assertEqual(response.status_code, 416)
            self.assertEqual(response.headers["content-range"], "bytes */7")
        for name in ("unsafe.html", "unsafe.svg", "file.docx"):
            self.connection.execute("UPDATE stored_file SET name=%s WHERE id=%s", (name, fixture["id"]))
            self.assertEqual(self.client.get(path).status_code, 415)
        factory.assert_not_called()
        self.connection.execute("UPDATE stored_file SET name='clip.mp4' WHERE id=%s", (fixture["id"],))
        client.get_object.return_value = {"Body": io.BytesIO(b"pri"), "ContentLength": 3, "ETag": '"test-etag"', "ContentRange": "bytes 1-3/7"}
        self.assertEqual(self.client.get(path, headers={"Range": "bytes=0-2"}).status_code, 503)
        self.connection.execute("UPDATE stored_file SET state='pending' WHERE id=%s", (fixture["id"],))
        factory.reset_mock()
        self.assertEqual(self.client.get(path).status_code, 404)
        self.connection.execute("UPDATE stored_file SET state='ready' WHERE id=%s", (fixture["id"],))
        self.client.cookies.delete("mikan_team_session")
        self.assertEqual(self.client.get(path).status_code, 401)
        factory.assert_not_called()

    @patch("files.boto3.client")
    def test_private_files_download_and_failures(self, factory):
        from botocore.exceptions import ClientError
        from psycopg.errors import ForeignKeyViolation
        for provider in ("r2", "s3"):
            with self.subTest(provider=provider):
                fixture = self.private_file_fixture(provider)
                client = factory.return_value
                client.get_object.side_effect = None
                factory.reset_mock()
                body = io.BytesIO(b"private")
                client.get_object.return_value = {"Body": body, "ContentLength": 7, "ETag": '"test-etag"', "ContentType": "text/html"}
                self.connection.execute("UPDATE stored_file SET name=%s WHERE id=%s", ('report\r\nInjected: yes.html', fixture["id"]))
                response = self.client.get(fixture["path"])
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.content, b"private")
                self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
                self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))
                self.assertNotIn("\r", response.headers["Content-Disposition"])
                self.assertNotIn("Injected", response.headers)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("sandbox", response.headers["Content-Security-Policy"])
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.assertEqual(response.headers["CDN-Cache-Control"], "no-store")
                self.assertNotIn("location", response.headers)
                self.assertTrue(body.closed)
                client.close.assert_called_once()
                expected = {"Bucket": fixture["payload"]["bucket"], "Key": "private/object", "IfMatch": '"test-etag"'}
                if provider == "s3":
                    expected["VersionId"] = "stored-version"
                client.get_object.assert_called_once_with(**expected)
                connection_path = f"/integrations/storage/{provider}/{fixture['storage_id']}"
                self.assertEqual(self.client.delete(connection_path).status_code, 409)
                self.assertEqual(self.client.put(connection_path, json={**fixture["payload"], "bucket": "changed-bucket"}).status_code, 409)
                rotated = {**fixture["payload"], "access_key": "rotated-access", "secret_key": "rotated-secret"}
                self.assertEqual(self.client.put(connection_path, json=rotated).status_code, 200)
                with self.assertRaises(ForeignKeyViolation), self.connection.transaction():
                    self.connection.execute("UPDATE stored_file SET company_id=-1 WHERE id=%s", (fixture["id"],))
                for field, value in (("ContentLength", 8), ("ETag", '"changed"')):
                    body = io.BytesIO(b"private")
                    client.get_object.return_value = {"Body": body, "ContentLength": 7, "ETag": '"test-etag"', field: value}
                    self.assertEqual(self.client.get(fixture["path"]).status_code, 503)
                    self.assertTrue(body.closed)
                client.get_object.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "secret-provider-detail"}}, "GetObject")
                response = self.client.get(fixture["path"])
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": "File temporarily unavailable."})
                self.assertIn("no-store", response.headers["Cache-Control"])

    @patch("files.boto3.client")
    def test_team_folder_configuration_and_manager_access(self, factory):
        path = "/company/teams/folders"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login()
        self.assertEqual(self.client.get(path).status_code, 401)
        fixture = self.private_file_fixture()
        settings = f"{path}/{self.team_id}"
        payload = {"storage_quota_bytes": 1000, "manager_can_view_drives": True}
        self.assertEqual(self.client.post(settings, json=payload, headers={"Origin": "https://untrusted.example"}).status_code, 403)
        self.assertEqual(self.client.post(settings, json={**payload, "storage_quota_bytes": 6}).status_code, 409)
        for invalid in (-1, True, 1.5, "1000", 9000000000000001):
            self.assertEqual(self.client.post(settings, json={**payload, "storage_quota_bytes": invalid}).status_code, 422)
        self.assertEqual(self.client.post(settings, json={**payload, "company_id": 1}).status_code, 422)
        other_company = self.client.post("/companies/", json=self.company_payload()).json()["id"]
        other_team = self.connection.execute("INSERT INTO team (name, company_id) VALUES ('Foreign', %s) RETURNING id", (other_company,)).fetchone()["id"]
        self.assertEqual(self.client.post(f"{path}/{other_team}", json=payload).status_code, 404)
        listing = self.client.get(path).json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["storage_used_bytes"], 7)
        self.assertFalse(listing["items"][0]["manager_can_view_drives"])
        self.assertEqual(self.client.get(path + "?search=missing").json()["total"], 0)
        self.assertEqual(self.client.get(path + "?from_date=2026-10-05&to_date=2026-10-04").status_code, 422)
        manager = self.connection.execute("""INSERT INTO team_account (company_id, team_id, name, email, mobile, role, status, auth_type)
            VALUES (%s, %s, 'Manager', %s, '9876543210', 'manager', 'active', 'otp') RETURNING id""", (self.company_id, self.team_id, f"manager-{secrets.token_hex(8)}@example.com")).fetchone()["id"]
        token = secrets.token_urlsafe(32)
        self.connection.execute("INSERT INTO team_session VALUES (%s, %s, clock_timestamp() + INTERVAL '1 hour')", (hash_token(token), manager))
        self.client.cookies.set("mikan_team_session", token)
        self.assertEqual(self.client.get(fixture["path"]).status_code, 404)
        factory.assert_not_called()
        self.assertEqual(self.client.post(settings, json=payload).status_code, 200)
        client = factory.return_value
        client.get_object.return_value = {"Body": io.BytesIO(b"private"), "ContentLength": 7, "ETag": '"test-etag"'}
        self.assertEqual(self.client.get(fixture["path"]).content, b"private")
        factory.reset_mock()
        sibling = self.connection.execute("INSERT INTO team (name, company_id, manager_can_view_drives) VALUES ('Sibling', %s, TRUE) RETURNING id", (self.company_id,)).fetchone()["id"]
        for company_id, team_id in ((self.company_id, sibling), (other_company, other_team)):
            with self.connection.transaction(force_rollback=True):
                self.connection.execute("UPDATE team_account SET company_id=%s, team_id=%s WHERE id=%s", (company_id, team_id, manager))
                self.assertEqual(self.client.get(fixture["path"]).status_code, 404)
        factory.assert_not_called()
        self.connection.execute("UPDATE team_account SET role='member' WHERE id=%s", (manager,))
        self.assertEqual(self.client.get(fixture["path"]).status_code, 404)
        self.connection.execute("UPDATE team_account SET role='manager' WHERE id=%s", (manager,))
        self.assertEqual(self.client.post(settings, json={**payload, "manager_can_view_drives": False}).status_code, 200)
        self.assertEqual(self.client.get(fixture["path"]).status_code, 404)
        self.client.cookies.delete("mikan_company_admin_session")
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post(settings, json=payload).status_code, 401)
        self.connection.execute("UPDATE team_account SET role='member' WHERE id=%s", (manager,))
        self.assertEqual(self.client.post(settings, json=payload).status_code, 401)

    def workflow_fixture(self):
        fixture = self.private_file_fixture()
        other_team = self.connection.execute("INSERT INTO team (company_id,name) VALUES (%s,'Reviewers') RETURNING id", (self.company_id,)).fetchone()["id"]
        reviewer = self.connection.execute("""INSERT INTO team_account (company_id,team_id,name,email,mobile,role,status,auth_type)
            VALUES (%s,%s,'Reviewer',%s,'9876543210','member','active','otp') RETURNING id""", (self.company_id, other_team, f"reviewer-{secrets.token_hex(8)}@example.com")).fetchone()["id"]
        token = secrets.token_urlsafe(32)
        self.connection.execute("INSERT INTO team_session VALUES (%s,%s,clock_timestamp()+INTERVAL '1 hour')", (hash_token(token), reviewer))
        nodes = [{"id": identifier, "type": kind, "position": {"x": 0, "y": 0}, "data": {"label": identifier, **data}} for identifier, kind, data in [
            ("start", "trigger", {}), ("review", "approval", {}), ("notice", "notify", {"message": "Approval complete"}),
            ("approved", "end", {"result": "approved"}), ("rejected", "end", {"result": "rejected"}), ("changes", "end", {"result": "changes_requested"})]]
        edges = [{"id": str(index), "source": source, "target": target, "sourceHandle": handle} for index, (source, target, handle) in enumerate([
            ("start", "review", "next"), ("review", "notice", "approved"), ("notice", "approved", "next"), ("review", "rejected", "rejected"), ("review", "changes", "changes_requested")])]
        payload = {"name": "File approval", "team_ids": [self.team_id], "submitter_roles": ["member", "manager"], "enabled": True, "graph": {"nodes": nodes, "edges": edges}}
        response = self.client.post("/company/teams/workflows", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return fixture, reviewer, token, response.json(), payload

    def test_manager_dashboard_reviews(self):
        fixture, reviewer, token, flow, payload = self.workflow_fixture()
        self.connection.execute("UPDATE team_account SET role='manager' WHERE id=ANY(%s)", ([fixture["account_id"], reviewer],))
        submitted = {"file_id": str(fixture["id"]), "request_key": str(uuid4()), "reviewers": {"review": [reviewer]}}
        response = self.client.post(f"/team/workflows/{flow['id']}/submit", json=submitted)
        self.assertEqual(response.status_code, 201, response.text)
        run_id = response.json()["id"]
        self.assertEqual(self.client.get("/team/dashboard").json()["pending_reviews"], 0)
        self.client.cookies.set("mikan_team_session", token)
        dashboard = self.client.get("/team/dashboard").json()
        self.assertEqual(dashboard["pending_reviews"], 1)
        self.assertEqual(dashboard["pending_reviews"], self.client.get("/team/workflows/runs?view=inbox").json()["total"])
        self.assertEqual(dashboard["files"], 0)
        self.assertEqual(dashboard["recent_files"], [])
        original_team = self.connection.execute("SELECT team_id FROM team_account WHERE id=%s", (reviewer,)).fetchone()["team_id"]
        self.connection.execute("UPDATE team_account SET role='member' WHERE id=%s", (fixture["account_id"],))
        self.connection.execute("UPDATE team_account SET team_id=%s WHERE id=%s", (self.team_id, reviewer))
        self.assertEqual(self.client.get("/team/dashboard").json()["pending_reviews"], 0)
        self.assertEqual(self.client.get("/team/workflows/runs?view=inbox").json()["total"], 0)
        self.connection.execute("UPDATE team_account SET team_id=%s WHERE id=%s", (original_team, reviewer))
        self.assertEqual(self.client.post(f"/team/workflows/runs/{run_id}/decide", json={"outcome": "approved"}).status_code, 200)
        self.assertEqual(self.client.get("/team/dashboard").json()["pending_reviews"], 0)
        self.assertEqual(self.client.get("/team/workflows/runs?view=inbox").json()["total"], 0)

    @patch("files.boto3.client")
    def test_workflow_execution_and_access(self, factory):
        fixture, reviewer, token, flow, payload = self.workflow_fixture()
        path = f"/team/workflows/{flow['id']}/submit"
        submitted = {"file_id": str(fixture["id"]), "request_key": str(uuid4()), "reviewers": {"review": [reviewer]}}
        response = self.client.post(path, json=submitted)
        self.assertEqual(response.status_code, 201, response.text)
        run_id = response.json()["id"]
        self.assertEqual(self.client.post(path, json=submitted).json()["id"], run_id)
        self.assertEqual(self.client.post(path, json={**submitted, "request_key": str(uuid4())}).status_code, 409)
        self.assertEqual(self.client.post(f"/team/workflows/runs/{run_id}/decide", json={"outcome": "approved"}).status_code, 403)
        self.assertEqual(self.client.get("/team/workflows/files").json()["total"], 1)
        self.assertEqual(self.client.get("/company/teams/workflows/runs").json()["total"], 1)
        self.client.cookies.set("mikan_team_session", token)
        self.assertEqual(self.client.get("/team/workflows/runs?view=inbox").json()["total"], 1)
        self.assertEqual(self.client.get("/team/workflows/files").json()["total"], 0)
        notices = self.client.get("/team/workflows/notifications").json()
        self.assertEqual(notices["total"], 1)
        self.assertEqual(self.client.post(f"/team/workflows/notifications/{notices['items'][0]['id']}/read", json={}).status_code, 200)
        factory.return_value.get_object.return_value = {"Body": io.BytesIO(b"content"), "ContentLength": 7, "ETag": '"test-etag"'}
        self.assertEqual(self.client.get(fixture["path"]).status_code, 200)
        self.assertEqual(self.client.post(f"/team/workflows/runs/{run_id}/decide", json={"outcome": "approved", "comment": "Checked"}).status_code, 200)
        self.assertEqual(self.client.post(f"/team/workflows/runs/{run_id}/decide", json={"outcome": "approved"}).status_code, 409)
        factory.reset_mock()
        self.assertEqual(self.client.get(fixture["path"]).status_code, 404)
        factory.assert_not_called()
        self.assertEqual(self.client.get("/team/workflows/runs?view=inbox").json()["total"], 0)
        self.client.cookies.set("mikan_team_session", fixture["token"])
        detail = self.client.get(f"/team/workflows/runs/{run_id}").json()
        self.assertEqual(detail["status"], "approved")
        self.assertTrue(any(event["kind"] == "approved" and event["actor_name"] == "Reviewer" for event in detail["events"]))
        self.assertNotIn("file_snapshot", detail)
        notices = self.client.get("/team/workflows/notifications").json()
        self.assertEqual(notices["total"], 4)
        self.assertTrue(any('submitted' in notice['message'] for notice in notices['items']))
        self.assertTrue(any('Reviewer approved:' in notice['message'] for notice in notices['items']))
        self.assertIn("Approval complete", [notice["message"] for notice in notices["items"]])
        self.assertEqual(self.client.get("/team/workflows/runs?from_date=2099-01-01").json()["total"], 0)
        self.assertEqual(self.client.get("/team/workflows/runs?from_date=2026-10-02&to_date=2026-10-01").status_code, 422)
        self.assertEqual(self.client.post(f"/company/teams/workflows/{flow['id']}", json={**payload, "version": 0}).status_code, 409)

    @patch("files.boto3.client")
    def test_workflow_policies_pause_and_snapshot(self, factory):
        fixture, reviewer, token, flow, payload = self.workflow_fixture()
        path = f"/team/workflows/{flow['id']}/submit"
        submitted = {"file_id": str(fixture["id"]), "request_key": str(uuid4()), "reviewers": {"review": [reviewer]}}
        self.assertEqual(self.client.post(path, json={**submitted, "reviewers": {"review": [fixture["account_id"]]}}).status_code, 422)
        payload["graph"]["nodes"][1]["data"]["reviewer_scope"] = "team"
        updated = self.client.post(f"/company/teams/workflows/{flow['id']}", json={**payload, "version": 1})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(self.client.post(path, json=submitted).status_code, 422)
        payload["graph"]["nodes"][1]["data"]["reviewer_scope"] = "company"
        self.assertEqual(self.client.post(f"/company/teams/workflows/{flow['id']}", json={**payload, "version": 2}).status_code, 200)
        run_id = self.client.post(path, json=submitted).json()["id"]
        self.assertEqual(self.client.post(f"/company/teams/workflows/{flow['id']}", json={**payload, "version": 3, "enabled": False}).status_code, 200)
        self.client.cookies.set("mikan_team_session", token)
        self.assertEqual(self.client.get(fixture["path"]).status_code, 404)
        self.assertEqual(self.client.post(f"/team/workflows/runs/{run_id}/decide", json={"outcome": "approved"}).status_code, 409)
        self.assertEqual(self.client.post(f"/company/teams/workflows/{flow['id']}", json={**payload, "version": 4}).status_code, 200)
        self.connection.execute("UPDATE stored_file SET etag='changed' WHERE id=%s", (fixture["id"],))
        self.assertEqual(self.client.get(fixture["path"]).status_code, 404)
        self.assertEqual(self.client.post(f"/team/workflows/runs/{run_id}/decide", json={"outcome": "approved"}).status_code, 409)
        self.assertEqual(self.client.post(f"/team/workflows/runs/{run_id}/cancel", json={}).status_code, 403)
        self.assertEqual(self.client.post(f"/company/teams/workflows/runs/{run_id}/cancel", json={"comment": "File changed"}).status_code, 200)
        factory.assert_not_called()

    def test_workflow_rejection_and_tenant_guards(self):
        fixture, reviewer, token, flow, payload = self.workflow_fixture()
        other_company = self.client.post("/companies/", json=self.company_payload()).json()["id"]
        foreign_team = self.connection.execute("INSERT INTO team(company_id,name) VALUES (%s,'Foreign') RETURNING id", (other_company,)).fetchone()["id"]
        foreign_person = self.connection.execute("""INSERT INTO team_account (company_id,team_id,name,email,mobile,role,status,auth_type)
            VALUES (%s,%s,'Foreign',%s,'9876543210','member','active','otp') RETURNING id""", (other_company, foreign_team, f"foreign-{secrets.token_hex(8)}@example.com")).fetchone()["id"]
        self.assertEqual(self.client.post("/company/teams/workflows", json={**payload, "team_ids": [foreign_team]}).status_code, 422)
        path = f"/team/workflows/{flow['id']}/submit"
        submitted = {"file_id": str(fixture["id"]), "request_key": str(uuid4()), "reviewers": {"review": [foreign_person]}}
        self.assertEqual(self.client.post(path, json=submitted).status_code, 422)
        for outcome in ["rejected", "changes_requested"]:
            self.client.cookies.set("mikan_team_session", fixture["token"])
            run_id = self.client.post(path, json={**submitted, "request_key": str(uuid4()), "reviewers": {"review": [reviewer]}}).json()["id"]
            self.client.cookies.set("mikan_team_session", token)
            self.assertEqual(self.client.post(f"/team/workflows/runs/{run_id}/decide", json={"outcome": outcome}).status_code, 422)
            self.assertEqual(self.client.post(f"/team/workflows/runs/{run_id}/decide", json={"outcome": outcome, "comment": "Please revise"}).status_code, 200)
            self.assertEqual(self.client.get(f"/team/workflows/runs/{run_id}").json()["status"], outcome)
        self.connection.execute("UPDATE team_account SET company_id=%s,team_id=%s WHERE id=%s", (other_company, foreign_team, reviewer))
        self.assertEqual(self.client.get(f"/team/workflows/runs/{run_id}").status_code, 404)
        self.assertEqual(self.client.get("/team/workflows/notifications").json()["total"], 0)
        self.assertEqual(self.client.get(f"/team/workflows/{flow['id']}").status_code, 404)
        self.client.cookies.delete("mikan_company_admin_session")
        self.assertEqual(self.client.get("/company/teams/workflows").status_code, 401)
        self.assertEqual(self.client.post("/company/teams/workflows", json=payload).status_code, 401)

    @patch("files.boto3.client")
    def test_workflow_multiple_reviewers_and_sequential_steps(self, factory):
        fixture, reviewer, token, flow, payload = self.workflow_fixture()
        admin_path = f"/company/teams/workflows/{flow['id']}"
        submit_path = f"/team/workflows/{flow['id']}/submit"
        for rule in ["all", "any"]:
            with self.subTest(rule=rule):
                self.client.cookies.set("mikan_team_session", fixture["token"])
                payload["graph"]["nodes"][1]["data"].update(rule=rule, allow_self_review=True)
                updated = self.client.post(admin_path, json={**payload, "version": flow["version"]})
                self.assertEqual(updated.status_code, 200, updated.text)
                flow = updated.json()
                submitted = {"file_id": str(fixture["id"]), "request_key": str(uuid4()), "version": flow["version"], "reviewers": {"review": [reviewer, fixture["account_id"]]}}
                self.assertEqual(self.client.post(submit_path, json={**submitted, "version": flow["version"] - 1}).status_code, 409)
                response = self.client.post(submit_path, json=submitted)
                self.assertEqual(response.status_code, 201, response.text)
                run_path = f"/team/workflows/runs/{response.json()['id']}"
                self.client.cookies.set("mikan_team_session", token)
                self.assertEqual(self.client.post(run_path + "/decide", json={"outcome": "approved"}).status_code, 200)
                detail = self.client.get(run_path).json()
                self.assertEqual(detail["status"], "pending" if rule == "all" else "approved")
                self.assertEqual(self.client.get(fixture["path"]).status_code, 404)
                self.client.cookies.set("mikan_team_session", fixture["token"])
                if rule == "all":
                    self.assertEqual(self.client.post(run_path + "/decide", json={"outcome": "approved"}).status_code, 200)
                else:
                    self.assertEqual(self.client.post(run_path + "/decide", json={"outcome": "approved"}).status_code, 409)
                    self.assertTrue(any(task["status"] == "cancelled" for task in detail["tasks"]))
                self.assertEqual(self.client.get(run_path).json()["status"], "approved")
        payload["graph"]["nodes"][1]["data"].update(rule="all", allow_self_review=False)
        payload["graph"]["nodes"].append({"id": "final_review", "type": "approval", "position": {"x": 200, "y": 200}, "data": {"label": "Final review", "reviewer_mode": "fixed", "reviewer_ids": [fixture["account_id"]], "allow_self_review": True}})
        payload["graph"]["edges"][1]["target"] = "final_review"
        payload["graph"]["edges"] += [{"id": f"final-{outcome}", "source": "final_review", "target": target, "sourceHandle": outcome} for outcome, target in [("approved", "notice"), ("rejected", "rejected"), ("changes_requested", "changes")]]
        self.assertEqual(self.client.post(admin_path, json={**payload, "version": flow["version"]}).status_code, 200)
        response = self.client.post(submit_path, json={"file_id": str(fixture["id"]), "request_key": str(uuid4()), "reviewers": {"review": [reviewer]}})
        self.assertEqual(response.status_code, 201, response.text)
        run_path = f"/team/workflows/runs/{response.json()['id']}"
        self.assertEqual(len(self.client.get(run_path).json()["tasks"]), 1)
        self.client.cookies.set("mikan_team_session", token)
        self.connection.execute("UPDATE team_account SET status='disabled' WHERE id=%s", (reviewer,))
        self.assertEqual(self.client.post(run_path + "/decide", json={"outcome": "approved"}).status_code, 401)
        self.connection.execute("UPDATE team_account SET status='active' WHERE id=%s", (reviewer,))
        self.assertEqual(self.client.post(run_path + "/decide", json={"outcome": "approved"}).status_code, 200)
        self.assertEqual(self.client.get(run_path).json()["current_node"], "final_review")
        self.assertEqual(self.client.get(fixture["path"]).status_code, 404)
        self.client.cookies.set("mikan_team_session", fixture["token"])
        self.assertEqual(self.client.post(run_path + "/decide", json={"outcome": "approved"}).status_code, 200)
        self.assertEqual(self.client.get(run_path).json()["status"], "approved")
        factory.assert_not_called()

    def test_workflow_graph_validation(self):
        nodes = [{"id": identifier, "type": kind, "position": {"x": 0, "y": 0}, "data": {"label": identifier}}
                 for identifier, kind in [("start", "trigger"), ("review", "approval"), ("done", "end")]]
        edges = [{"id": "start-review", "source": "start", "target": "review", "sourceHandle": "next"}]
        edges += [{"id": outcome, "source": "review", "target": "done", "sourceHandle": outcome} for outcome in ["approved", "rejected", "changes_requested"]]
        self.assertEqual(validate_graph(Graph(nodes=nodes, edges=edges)), "start")
        with self.assertRaisesRegex(ValueError, "every outcome"):
            validate_graph(Graph(nodes=nodes, edges=edges[:-1]))
        with self.assertRaisesRegex(ValueError, "cycles"):
            validate_graph(Graph(nodes=nodes, edges=[{**edge, "target": "review"} if edge["id"] == "approved" else edge for edge in edges]))
        with self.assertRaisesRegex(ValueError, "reachable"):
            validate_graph(Graph(nodes=nodes + [{**nodes[-1], "id": "orphan"}], edges=edges))
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_graph(Graph(nodes=nodes + [nodes[-1]], edges=edges))
        nodes[0]["data"]["trigger"] = "upload"
        with self.assertRaisesRegex(ValueError, "fixed reviewers"):
            validate_graph(Graph(nodes=nodes, edges=edges))
        nodes[1]["data"].update(reviewer_mode="fixed", reviewer_ids=[1])
        self.assertEqual(validate_graph(Graph(nodes=nodes, edges=edges)), "start")
        nodes[1]["type"] = "file"
        nodes[1]["data"].update(file_action="move", target="../another-drive")
        with self.assertRaisesRegex(ValueError, "relative folder"):
            validate_graph(Graph(nodes=nodes, edges=[edges[0], {"id": "finish", "source": "review", "target": "done", "sourceHandle": "next"}]))

    def test_team_folder_activity(self):
        fixture = self.private_file_fixture()
        path = f"/company/teams/folders/{self.team_id}/activity"
        settings = f"/company/teams/folders/{self.team_id}"
        initial = self.client.get(path).json()
        self.assertEqual(initial["total"], 2)
        creation = initial["items"][-1]
        self.assertEqual(creation["kind"], "created")
        self.assertEqual(creation["created_at"], initial["team"]["created_at"])
        self.assertTrue(creation["actor_name"])
        payload = {"storage_quota_bytes": 2000000000, "manager_can_view_drives": True}
        self.assertEqual(self.client.post(settings, json=payload).status_code, 200)
        result = self.client.get(path).json()
        event = result["items"][0]
        self.assertEqual(result["total"], 3)
        self.assertEqual(event["kind"], "settings_changed")
        self.assertEqual(event["previous_quota_bytes"], 1000000000)
        self.assertEqual(event["quota_bytes"], 2000000000)
        self.assertFalse(event["previous_manager_access"])
        self.assertTrue(event["manager_access"])
        self.assertEqual(event["actor_name"], creation["actor_name"])
        self.assertEqual(self.client.post(settings, json=payload).status_code, 200)
        self.assertEqual(self.client.post(settings, json={**payload, "storage_quota_bytes": 0}).status_code, 409)
        self.assertEqual(self.client.get(path).json()["total"], 3)
        for quota in range(10, 21):
            self.assertEqual(self.client.post(settings, json={**payload, "storage_quota_bytes": quota}).status_code, 200)
        first = self.client.get(path).json()
        second = self.client.get(path + "?page=2").json()
        self.assertEqual(first["total"], 14)
        self.assertEqual(len(first["items"]), 10)
        self.assertEqual(len(second["items"]), 4)
        self.assertFalse({item["id"] for item in first["items"]} & {item["id"] for item in second["items"]})
        self.assertEqual(self.client.get(path + "?to_date=2000-01-01").json()["total"], 0)
        self.assertEqual(self.client.get(path + "?from_date=2026-10-05&to_date=2026-10-04").status_code, 422)
        other_company = self.client.post("/companies/", json=self.company_payload()).json()["id"]
        other_team = self.connection.execute("INSERT INTO team (name, company_id) VALUES ('Other', %s) RETURNING id", (other_company,)).fetchone()["id"]
        self.assertEqual(self.client.get(f"/company/teams/folders/{other_team}/activity").status_code, 404)
        self.assertEqual(self.client.post(path, json={}).status_code, 405)
        self.client.cookies.delete("mikan_company_admin_session")
        self.assertEqual(self.client.get(path).status_code, 401)
        self.connection.execute("UPDATE team_account SET role='manager' WHERE id=%s", (fixture["account_id"],))
        self.assertEqual(self.client.get(path).status_code, 401)
        self.client.cookies.delete("mikan_team_session")
        self.assertEqual(self.client.get(path).status_code, 401)

    def test_team_folder_quota_accounting(self):
        from psycopg.errors import CheckViolation
        fixture = self.private_file_fixture()
        def used():
            return self.connection.execute("SELECT storage_used_bytes FROM team WHERE id=%s", (self.team_id,)).fetchone()["storage_used_bytes"]
        self.client.post(f"/company/teams/folders/{self.team_id}", json={"storage_quota_bytes": 10, "manager_can_view_drives": False})
        storage = self.client.post("/integrations/storage/s3", json={**fixture["payload"], "account_id": None, "region": "ap-south-1"}).json()["id"]
        def register(size):
            return self.connection.execute("""INSERT INTO stored_file (company_id, team_id, owner_id, connection_id, object_key, etag, name, size_bytes)
                VALUES (%s, %s, %s, %s, %s, 'etag', 'second', %s) RETURNING id""", (self.company_id, self.team_id, fixture["account_id"], storage, secrets.token_hex(8), size)).fetchone()["id"]
        with self.assertRaises(CheckViolation), self.connection.transaction():
            register(4)
        second_file = register(3)
        self.assertEqual(used(), 10)
        self.assertEqual(self.client.get("/company/teams/folders").json()["items"][0]["storage_used_bytes"], 10)
        self.connection.execute("DELETE FROM stored_file WHERE id=%s", (second_file,))
        self.assertEqual(used(), 7)
        with self.assertRaises(CheckViolation), self.connection.transaction():
            self.connection.execute("UPDATE stored_file SET size_bytes=11 WHERE id=%s", (fixture["id"],))
        self.assertEqual(used(), 7)
        self.connection.execute("UPDATE stored_file SET size_bytes=10, state='pending' WHERE id=%s", (fixture["id"],))
        self.assertEqual(used(), 10)
        for state in ("ready", "quarantined", "trashed"):
            self.connection.execute("UPDATE stored_file SET state=%s WHERE id=%s", (state, fixture["id"]))
            self.assertEqual(used(), 10)
        self.connection.execute("DELETE FROM stored_file WHERE id=%s", (fixture["id"],))
        self.assertEqual(used(), 0)
        self.assertEqual(self.client.post(f"/company/teams/folders/{self.team_id}", json={"storage_quota_bytes": 0, "manager_can_view_drives": False}).status_code, 200)
        with self.assertRaises(CheckViolation), self.connection.transaction():
            register(0)

    def test_storage_configuration_security(self):
        path = "/integrations/storage/r2"
        payload = {"name": "Test R2", "bucket": "test-" + secrets.token_hex(8), "account_id": "a" * 32, "region": "auto", "access_key": "test-access", "secret_key": "test-secret", "enabled": True}
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login()
        self.assertEqual(self.client.post(path, json=payload, headers={"Origin": "https://untrusted.example"}).status_code, 403)
        self.assertEqual(self.client.post(path, json={**payload, "endpoint": "http://127.0.0.1"}).status_code, 422)
        self.assertEqual(self.client.post(path, json={**payload, "endpoint": "https://" + "b" * 32 + ".r2.cloudflarestorage.com"}).status_code, 422)
        self.assertEqual(self.client.post(path, json={**payload, "access_key": None}).status_code, 422)
        created = self.client.post(path, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        identifier = created.json()["id"]
        self.assertNotIn("test-secret", created.text)
        self.assertNotIn("test-access", created.text)
        stored = self.connection.execute("SELECT * FROM storage_connection WHERE id=%s", (identifier,)).fetchone()
        self.assertNotEqual(stored["secret_key_encrypted"], payload["secret_key"])
        self.assertNotEqual(stored["access_key_encrypted"], payload["access_key"])
        self.assertEqual(self.client.post(path, json=payload).status_code, 409)
        updated = self.client.put(f"{path}/{identifier}", json={**payload, "name": "Renamed", "access_key": None, "secret_key": None})
        self.assertEqual(updated.status_code, 200, updated.text)
        retained = self.connection.execute("SELECT * FROM storage_connection WHERE id=%s", (identifier,)).fetchone()
        self.assertEqual(stored["secret_key_encrypted"], retained["secret_key_encrypted"])
        rotated = self.client.put(f"{path}/{identifier}", json={**payload, "name": "Renamed", "access_key": "rotated-access", "secret_key": "rotated-secret"})
        self.assertEqual(rotated.status_code, 200)
        changed = self.connection.execute("SELECT * FROM storage_connection WHERE id=%s", (identifier,)).fetchone()
        self.assertNotEqual(retained["access_key_encrypted"], changed["access_key_encrypted"])
        self.assertNotEqual(retained["secret_key_encrypted"], changed["secret_key_encrypted"])
        invalid = self.client.post(path, json={**payload, "secret_key": "private-secret\ninvalid"})
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn("private-secret", invalid.text)
        extra = self.client.post(path, json={**payload, "bucket": "second-" + secrets.token_hex(8)})
        self.assertEqual(extra.status_code, 201)
        self.assertEqual(self.client.get(path + "?search=Renamed").json()["total"], 1)
        self.assertEqual(self.client.get(path + "?from_date=2026-10-05&to_date=2026-10-04").status_code, 422)
        listing = self.client.get(path).text
        self.assertNotIn("encrypted", listing)
        self.assertNotIn("test-secret", listing)
        aws = {**payload, "name": "AWS", "account_id": None, "region": "ap-south-1"}
        self.assertEqual(self.client.post("/integrations/storage/s3", json=aws).status_code, 201)
        self.assertEqual(self.client.post("/integrations/storage/s3", json={**aws, "region": "invalid"}).status_code, 422)
        self.assertEqual(self.client.delete(f"/integrations/storage/s3/{identifier}").status_code, 404)
        self.assertEqual(self.client.delete(f"{path}/{identifier}").status_code, 200)
        self.assertEqual(self.client.delete(f"{path}/{identifier}").status_code, 404)
        self.prepare_team()
        self.client.cookies.delete("mikan_admin_session")
        self.assertEqual(self.client.get("/integrations/storage").status_code, 401)
        self.assertEqual(self.client.post(path, json=payload).status_code, 401)

    @patch("storage.boto3.client")
    def test_storage_connection_test_and_cleanup(self, factory):
        from botocore.exceptions import ClientError
        self.login()
        payload = {"name": "Test AWS", "bucket": "test-" + secrets.token_hex(8), "region": "ap-south-1", "access_key": "test-access", "secret_key": "test-secret"}
        created = self.client.post("/integrations/storage/s3", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        identifier = created.json()["id"]
        path = f"/integrations/storage/s3/{identifier}/test"
        client = factory.return_value
        del client.__enter__
        del client.__exit__
        client.put_object.return_value = {"VersionId": "test-version"}
        client.get_object.return_value = {"Body": io.BytesIO(b"Mikan storage connection test\n")}
        self.assertEqual(self.client.post(path, json={}).status_code, 422)
        response = self.client.post(path, json={"confirm": True})
        self.assertEqual(response.json()["status"], "passed", response.text)
        client.close.assert_called_once()
        self.assertEqual(client.delete_object.call_args.kwargs["VersionId"], "test-version")
        self.assertEqual(factory.call_args.kwargs["aws_secret_access_key"], "test-secret")
        self.assertEqual(self.client.post(path, json={"confirm": True}).status_code, 429)
        self.connection.execute("UPDATE storage_connection SET last_test_at=NULL WHERE id=%s", (identifier,))
        client.get_object.return_value = {"Body": io.BytesIO(b"wrong")}
        self.assertEqual(self.client.post(path, json={"confirm": True}).json()["status"], "failed")
        self.connection.execute("UPDATE storage_connection SET last_test_at=NULL WHERE id=%s", (identifier,))
        client.get_object.return_value = {"Body": io.BytesIO(b"Mikan storage connection test\n")}
        client.delete_object.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "private-provider-detail"}}, "DeleteObject")
        response = self.client.post(path, json={"confirm": True})
        self.assertEqual(response.json()["status"], "cleanup_failed")
        self.assertTrue(response.json()["cleanup_key"].startswith(".mikan-connection-tests/"))
        self.assertNotIn("private-provider-detail", response.text)
        self.assertNotIn("test-secret", response.text)
        latest = next(item for item in self.client.get("/integrations/storage/s3").json()["items"] if item["id"] == identifier)
        self.assertEqual(latest["last_cleanup"]["key"], response.json()["cleanup_key"])
        self.assertEqual(latest["last_cleanup"]["bucket"], payload["bucket"])
        self.connection.execute("UPDATE storage_connection SET last_test_at=NULL WHERE id=%s", (identifier,))
        client.delete_object.side_effect = None
        client.head_bucket.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "secret"}}, "HeadBucket")
        client.reset_mock()
        self.assertEqual(self.client.post(path, json={"confirm": True}).json()["status"], "failed")
        client.put_object.assert_not_called()
        client.delete_object.assert_not_called()
        self.connection.execute("UPDATE storage_connection SET last_test_at=NULL WHERE id=%s", (identifier,))
        client.head_bucket.side_effect = None
        client.put_object.side_effect = ClientError({"Error": {"Code": "RequestTimeout", "Message": "private"}}, "PutObject")
        response = self.client.post(path, json={"confirm": True})
        self.assertEqual(response.json()["status"], "cleanup_failed")
        client.delete_object.assert_called_once()
        self.assertNotIn("VersionId", client.delete_object.call_args.kwargs)
        self.assertIn("Upload failed: RequestTimeout", response.json()["detail"])
        self.connection.execute("UPDATE storage_connection SET last_test_at=NULL WHERE id=%s", (identifier,))
        client.delete_object.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "private-cleanup"}}, "DeleteObject")
        response = self.client.post(path, json={"confirm": True})
        self.assertIn("Upload failed: RequestTimeout", response.json()["detail"])
        self.assertIn("Cleanup failed: AccessDenied", response.json()["detail"])
        self.assertIn("does not prove an object exists", response.json()["detail"])
        self.assertNotIn("private", response.text)
        self.connection.execute("UPDATE storage_connection SET last_test_at=NULL WHERE id=%s", (identifier,))
        client.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "private-upload"}, "ResponseMetadata": {"HTTPStatusCode": 403}}, "PutObject")
        client.delete_object.reset_mock()
        response = self.client.post(path, json={"confirm": True})
        self.assertEqual(response.json()["status"], "failed")
        self.assertIn("Upload failed: AccessDenied", response.json()["detail"])
        self.assertIsNone(response.json()["cleanup_key"])
        client.delete_object.assert_not_called()
        self.assertNotIn("private-upload", response.text)

    @patch("storage.boto3.client")
    def test_r2_connection_omits_unsupported_version_id(self, factory):
        self.login()
        payload = {"name": "Test R2", "bucket": "test-" + secrets.token_hex(8),
                   "account_id": "a" * 32, "access_key": "test-access", "secret_key": "test-secret"}
        created = self.client.post("/integrations/storage/r2", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        client = factory.return_value
        client.put_object.return_value = {"VersionId": "r2-returned-version"}
        client.get_object.return_value = {"Body": io.BytesIO(b"Mikan storage connection test\n")}
        response = self.client.post(f"/integrations/storage/r2/{created.json()['id']}/test", json={"confirm": True})
        self.assertEqual(response.json()["status"], "passed", response.text)
        self.assertNotIn("VersionId", client.get_object.call_args.kwargs)
        self.assertNotIn("VersionId", client.delete_object.call_args.kwargs)
        client.close.assert_called_once()

    def prepare_team(self):
        self.login()
        self.client.put("/integrations/zeptomail", json={"endpoint": "https://cpaas.zoho.in/v1.1/email", "sender_email": self.email, "sender_name": "Test", "token": "test-token", "enabled": True})
        company = self.client.post("/companies/", json=self.company_payload()).json()
        admin_email = f"company-{secrets.token_hex(8)}@example.com"
        self.client.post("/company-admins/", json={"name": "Company Admin", "email": admin_email, "mobile": "+919876543210", "password": self.password, "company_id": company["id"]})
        self.assertEqual(self.client.post("/auth/company/admin/login", json={"email": admin_email, "password": self.password}).status_code, 200)
        team = self.client.post("/company/teams/", json={"name": "Engineering"})
        self.assertEqual(team.status_code, 201, team.text)
        self.team_id = team.json()["id"]
        self.company_id = company["id"]
        return {"name": "Team Person", "email": f"member-{secrets.token_hex(8)}@example.com", "mobile": "+919876543210", "role": "member", "team_id": self.team_id}

    def invite_team_person(self, mail, payload):
        result = self.client.post("/company/teams/invite", json=payload)
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["detail"], "Member Invited successfully.")
        token = re.search(r"#token=([\w-]+)", mail.call_args.args[3]).group(1)
        self.assertNotIn(token, result.text)
        stored = self.connection.execute("SELECT token_hash FROM team_verification WHERE account_id = %s", (result.json()["id"],)).fetchone()
        self.assertEqual(stored["token_hash"], hash_token(token))
        return result.json()["id"], token

    @patch("teams.send_email")
    def test_team_password_activation_and_isolation(self, mail):
        payload = self.prepare_team()
        account_id, token = self.invite_team_person(mail, payload)
        self.assertEqual(self.client.post("/auth/team/password", json={"email": payload["email"], "password": self.password}).status_code, 401)
        details = self.client.post("/auth/team/verification", json={"token": token})
        self.assertEqual(details.json()["team_name"], "Engineering")
        activation = {"token": token, "auth_type": "password", "password": self.password}
        self.assertEqual(self.client.post("/auth/team/activate", json=activation).status_code, 200)
        self.assertEqual(self.client.post("/auth/team/activate", json=activation).status_code, 400)
        self.client.cookies.clear()
        response = self.client.post("/auth/team/password", json={"email": payload["email"].upper(), "password": self.password})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assert_session_lifetime(response, "mikan_team_session")
        self.assertEqual(self.client.get("/auth/team/me").json()["id"], account_id)
        self.assertEqual(self.client.get("/company/teams/").status_code, 401)
        self.assertEqual(self.client.get("/auth/company/admin/me").status_code, 401)
        self.assertEqual(self.client.get("/auth/admin/me").status_code, 401)
        self.assertEqual(self.client.get("/team/people").status_code, 403)
        self.assertEqual(self.client.get("/team/people?role=manager").status_code, 403)
        team_token = self.client.cookies.get("mikan_team_session")
        self.client.cookies.set("mikan_company_admin_session", team_token)
        self.assertEqual(self.client.get("/company/teams/").status_code, 401)
        self.assertEqual(self.client.post("/auth/team/logout").status_code, 200)
        self.assertEqual(self.client.get("/auth/team/me").status_code, 401)
        mail.reset_mock()
        self.client.post("/auth/team/otp/request", json={"email": payload["email"]})
        mail.assert_not_called()

    @patch("teams.send_email")
    def test_team_manager_directory_isolation(self, mail):
        payload = {**self.prepare_team(), "role": "manager"}
        account_id, token = self.invite_team_person(mail, payload)
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": token, "auth_type": "password", "password": self.password}).status_code, 200)
        self.assertEqual(self.client.post("/auth/team/password", json={"email": payload["email"], "password": self.password}).status_code, 200)
        member = {**payload, "role": "member", "name": "Colleague", "email": f"colleague-{secrets.token_hex(8)}@example.com"}
        member_id, token = self.invite_team_person(mail, member)
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": token, "auth_type": "otp"}).status_code, 200)
        other_team = self.client.post("/company/teams/", json={"name": "Other team"}).json()["id"]
        foreign_company = self.client.post("/companies/", json=self.company_payload()).json()["id"]
        foreign_team = self.connection.execute("INSERT INTO team (name, company_id) VALUES ('Foreign', %s) RETURNING id", (foreign_company,)).fetchone()["id"]
        for company_id, team_id in ((self.company_id, other_team), (foreign_company, foreign_team)):
            self.connection.execute("INSERT INTO team_account (company_id, team_id, name, email, mobile, role, status, auth_type, activated_at) VALUES (%s, %s, 'Private', %s, '9876543210', 'member', 'active', 'otp', clock_timestamp())", (company_id, team_id, f"private-{secrets.token_hex(8)}@example.com"))
        response = self.client.get(f"/team/people?team_id={foreign_team}&company_id={foreign_company}")
        self.assertEqual(response.status_code, 200, response.text)
        people = response.json()
        self.assertEqual(people["total"], 2)
        self.assertEqual({person["id"] for person in people["items"]}, {account_id, member_id})
        for person in people["items"]:
            self.assertNotIn("email", person)
            self.assertNotIn("mobile", person)
        self.assertEqual(self.client.get("/team/people?search=Colleague").json()["total"], 1)
        self.assertEqual(self.client.post(f"/company/teams/people/{account_id}/edit", json={**payload, "role": "member"}).status_code, 200)
        self.assertEqual(self.client.get("/team/people").status_code, 401)
        self.assertEqual(self.client.post("/auth/team/password", json={"email": payload["email"], "password": self.password}).status_code, 200)
        self.assertEqual(self.client.get("/auth/team/me").json()["role"], "member")
        self.assertEqual(self.client.get("/team/people").status_code, 403)

    @patch("teams.send_email")
    def test_team_management_constraints(self, mail):
        payload = self.prepare_team()
        self.assertEqual(self.client.post("/company/teams/", json={"name": " engineering "}).status_code, 409)
        payload["role"] = "manager"
        account_id, token = self.invite_team_person(mail, payload)
        other = {**payload, "email": f"other-{secrets.token_hex(8)}@example.com"}
        self.assertEqual(self.client.post("/company/teams/invite", json=other).status_code, 409)
        self.assertEqual(self.client.post("/company/teams/invite", json={**other, "company_id": 999}).status_code, 422)
        self.assertEqual(self.client.post("/company/teams/invite", json={**other, "role": "super_admin"}).status_code, 422)
        teams = self.client.get("/company/teams/").json()
        self.assertEqual(teams["items"][0]["manager"], payload["name"])
        self.assertEqual(teams["items"][0]["manager_status"], "invited")
        foreign_company = self.client.post("/companies/", json=self.company_payload()).json()["id"]
        foreign_team = self.connection.execute("INSERT INTO team (name, company_id) VALUES ('Other', %s) RETURNING id", (foreign_company,)).fetchone()["id"]
        self.assertEqual(self.client.post("/company/teams/invite", json={**other, "team_id": foreign_team}).status_code, 422)
        foreign_person = self.connection.execute("INSERT INTO team_account (company_id, team_id, name, email, mobile, role) VALUES (%s, %s, 'Private', %s, '9876543210', 'member') RETURNING id", (foreign_company, foreign_team, other["email"])).fetchone()["id"]
        self.assertEqual(self.client.get(f"/company/teams/people?team_id={foreign_team}").json()["total"], 0)
        self.assertEqual(self.client.post(f"/company/teams/people/{foreign_person}/disable").status_code, 404)
        self.assertEqual(self.client.post(f"/company/teams/people/{foreign_person}/resend").status_code, 404)
        self.assertEqual(self.client.get("/company/teams/people?search=missing").json()["total"], 0)
        self.assertEqual(self.client.get("/company/teams/?from_date=2026-12-01&to_date=2026-01-01").status_code, 422)
        self.assertEqual(self.client.post(f"/company/teams/people/{account_id}/disable").status_code, 200)
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": token, "auth_type": "otp"}).status_code, 400)
        self.assertIsNone(self.client.get("/company/teams/").json()["items"][0]["manager"])

    @patch("teams.send_email")
    def test_team_member_edit_permissions_and_assignments(self, mail):
        payload = self.prepare_team()
        account_id, token = self.invite_team_person(mail, payload)
        path = f"/company/teams/people/{account_id}/edit"
        self.client.post("/auth/team/activate", json={"token": token, "auth_type": "password", "password": self.password})
        self.client.post("/auth/team/password", json={"email": payload["email"], "password": self.password})
        mail.reset_mock()
        edited = {**payload, "name": "Updated Member", "mobile": "+919876543211"}
        self.assertEqual(self.client.post(path, json=edited).status_code, 200)
        identity = self.client.get("/auth/team/me").json()
        self.assertEqual(identity["name"], edited["name"])
        self.assertEqual(identity["mobile"], edited["mobile"])
        self.assertEqual(identity["auth_type"], "password")
        mail.assert_not_called()
        self.assertEqual(self.client.post(path, json=edited, headers={"Origin": "https://untrusted.example"}).status_code, 403)
        for field, value in (("mobile", "invalid"), ("name", " "), ("role", "super_admin"), ("company_id", 999)):
            self.assertEqual(self.client.post(path, json={**edited, field: value}).status_code, 422)
        foreign_company = self.client.post("/companies/", json=self.company_payload()).json()["id"]
        foreign_team = self.connection.execute("INSERT INTO team (name, company_id) VALUES ('Other', %s) RETURNING id", (foreign_company,)).fetchone()["id"]
        foreign_person = self.connection.execute("INSERT INTO team_account (company_id, team_id, name, email, mobile, role) VALUES (%s, %s, 'Private', %s, '9876543210', 'member') RETURNING id", (foreign_company, foreign_team, f"private-{secrets.token_hex(8)}@example.com")).fetchone()["id"]
        self.assertEqual(self.client.post(path, json={**edited, "team_id": foreign_team}).status_code, 422)
        self.assertEqual(self.client.post(f"/company/teams/people/{foreign_person}/edit", json=edited).status_code, 404)
        manager = {**payload, "role": "manager", "email": f"manager-{secrets.token_hex(8)}@example.com"}
        manager_id, _ = self.invite_team_person(mail, manager)
        self.assertEqual(self.client.get("/company/teams/").json()["items"][0]["manager_id"], manager_id)
        self.assertEqual(self.client.post(f"/company/teams/people/{manager_id}/edit", json={**manager, "name": "Updated Manager"}).status_code, 200)
        self.assertEqual(self.client.post(path, json={**edited, "role": "manager"}).status_code, 409)
        self.assertEqual(self.client.post(path, json={**edited, "email": manager["email"]}).status_code, 409)
        new_team = self.client.post("/company/teams/", json={"name": "Operations"}).json()["id"]
        moved = {**edited, "team_id": new_team, "role": "manager"}
        self.assertEqual(self.client.post(path, json=moved).status_code, 200)
        self.assertEqual(self.client.get("/auth/team/me").status_code, 401)
        self.assertEqual(self.client.post("/auth/team/password", json={"email": payload["email"], "password": self.password}).status_code, 200)
        self.assertEqual(self.client.get("/auth/team/me").json()["team_id"], new_team)
        company_token = self.client.cookies.get("mikan_company_admin_session")
        self.client.cookies.clear()
        self.assertEqual(self.client.post(path, json=moved).status_code, 401)
        self.client.cookies.set("mikan_company_admin_session", company_token)
        self.client.post(f"/company/teams/people/{account_id}/disable")
        self.assertEqual(self.client.post(path, json=moved).status_code, 409)

    @patch("teams.send_email")
    def test_team_member_email_edit_reverification_and_rollback(self, mail):
        payload = self.prepare_team()
        account_id, old_token = self.invite_team_person(mail, payload)
        path = f"/company/teams/people/{account_id}/edit"
        changed = {**payload, "email": f"changed-{secrets.token_hex(8)}@example.com"}
        self.assertEqual(self.client.post(path, json=changed).status_code, 200)
        token = re.search(r"#token=([\w-]+)", mail.call_args.args[3]).group(1)
        self.assertEqual(mail.call_args.args[1], changed["email"])
        self.assertEqual(self.client.post("/auth/team/verification", json={"token": old_token}).status_code, 400)
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": token, "auth_type": "password", "password": self.password}).status_code, 200)
        self.assertEqual(self.client.post("/auth/team/password", json={"email": changed["email"], "password": self.password}).status_code, 200)
        final = {**changed, "email": f"final-{secrets.token_hex(8)}@example.com"}
        mail.side_effect = HTTPException(502, "Provider unavailable")
        self.assertEqual(self.client.post(path, json=final).status_code, 502)
        self.assertEqual(self.client.get("/auth/team/me").json()["email"], changed["email"])
        mail.side_effect = None
        self.assertEqual(self.client.post(path, json=final).status_code, 200)
        token = re.search(r"#token=([\w-]+)", mail.call_args.args[3]).group(1)
        self.assertEqual(self.client.get("/auth/team/me").status_code, 401)
        stored = self.connection.execute("SELECT * FROM team_account WHERE id = %s", (account_id,)).fetchone()
        self.assertEqual(stored["status"], "invited")
        self.assertIsNone(stored["password_hash"])
        self.assertIsNone(stored["auth_type"])
        self.assertEqual(self.client.post("/auth/team/password", json={"email": changed["email"], "password": self.password}).status_code, 401)
        self.assertEqual(self.client.post("/auth/team/password", json={"email": final["email"], "password": self.password}).status_code, 401)
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": token, "auth_type": "otp"}).status_code, 200)

    @patch("teams.send_email")
    def test_team_otp_choice_expiry_reuse_and_limits(self, mail):
        payload = self.prepare_team()
        account_id, token = self.invite_team_person(mail, payload)
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": token, "auth_type": "otp", "password": self.password}).status_code, 422)
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": token, "auth_type": "otp"}).status_code, 200)
        self.assertEqual(self.client.post("/auth/team/password", json={"email": payload["email"], "password": self.password}).status_code, 401)
        request = self.client.post("/auth/team/otp/request", json={"email": payload["email"]})
        self.assertEqual(request.status_code, 202, request.text)
        challenge = request.json()["challenge"]
        code = re.search(r"code is (\d{6})", mail.call_args.args[3]).group(1)
        self.assertNotIn(code, request.text)
        stored = self.connection.execute("SELECT * FROM team_otp WHERE account_id = %s", (account_id,)).fetchone()
        self.assertEqual(stored["code_hash"], hash_token(challenge + code))
        self.assertEqual(self.client.post("/auth/team/otp/request", json={"email": payload["email"]}).status_code, 429)
        wrong = "000000" if code != "000000" else "111111"
        self.assertEqual(self.client.post("/auth/team/otp/verify", json={"challenge": challenge, "code": wrong}).status_code, 401)
        response = self.client.post("/auth/team/otp/verify", json={"challenge": challenge, "code": code})
        self.assertEqual(response.status_code, 200)
        self.assert_session_lifetime(response, "mikan_team_session")
        self.assertEqual(self.client.post("/auth/team/otp/verify", json={"challenge": challenge, "code": code}).status_code, 401)
        self.assertEqual(self.client.get("/auth/team/me").json()["auth_type"], "otp")
        self.connection.execute("DELETE FROM team_rate_limit")
        challenge = self.client.post("/auth/team/otp/request", json={"email": payload["email"]}).json()["challenge"]
        code = re.search(r"code is (\d{6})", mail.call_args.args[3]).group(1)
        self.connection.execute("UPDATE team_otp SET expires_at = clock_timestamp() - INTERVAL '1 second' WHERE account_id = %s", (account_id,))
        self.assertEqual(self.client.post("/auth/team/otp/verify", json={"challenge": challenge, "code": code}).status_code, 401)
        self.connection.execute("UPDATE team_otp SET expires_at = clock_timestamp() + INTERVAL '10 minutes' WHERE account_id = %s", (account_id,))
        self.connection.execute("DELETE FROM team_rate_limit")
        for attempt in range(5):
            self.assertEqual(self.client.post("/auth/team/otp/verify", json={"challenge": challenge, "code": "abcdef"}).status_code, 401)
        self.assertEqual(self.client.post("/auth/team/otp/verify", json={"challenge": challenge, "code": code}).status_code, 429)
        self.assertEqual(self.client.post(f"/company/teams/people/{account_id}/disable").status_code, 200)
        self.assertEqual(self.client.get("/auth/team/me").status_code, 401)

    @patch("teams.send_email")
    def test_team_resend_recovery_and_mail_failure(self, mail):
        payload = self.prepare_team()
        mail.side_effect = HTTPException(502, "Provider unavailable")
        self.assertEqual(self.client.post("/company/teams/invite", json=payload).status_code, 502)
        self.assertEqual(self.client.get("/company/teams/people").json()["total"], 0)
        mail.side_effect = None
        account_id, old_token = self.invite_team_person(mail, payload)
        self.assertEqual(self.client.post(f"/company/teams/people/{account_id}/resend").status_code, 429)
        self.connection.execute("UPDATE team_verification SET created_at = clock_timestamp() - INTERVAL '2 minutes' WHERE account_id = %s", (account_id,))
        self.assertEqual(self.client.post(f"/company/teams/people/{account_id}/resend").status_code, 200)
        token = re.search(r"#token=([\w-]+)", mail.call_args.args[3]).group(1)
        self.assertNotEqual(token, old_token)
        self.assertEqual(self.client.post("/auth/team/verification", json={"token": old_token}).status_code, 400)
        self.connection.execute("UPDATE team_verification SET expires_at = clock_timestamp() - INTERVAL '1 second' WHERE account_id = %s", (account_id,))
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": token, "auth_type": "otp"}).status_code, 400)
        self.connection.execute("UPDATE team_verification SET expires_at = clock_timestamp() + INTERVAL '1 hour' WHERE account_id = %s", (account_id,))
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": token, "auth_type": "password", "password": self.password}).status_code, 200)
        self.assertEqual(self.client.post("/auth/team/password", json={"email": payload["email"], "password": self.password}).status_code, 200)
        self.assertEqual(self.client.post("/auth/team/recover", json={"email": payload["email"]}).status_code, 202)
        recovery = re.search(r"#token=([\w-]+)", mail.call_args.args[3]).group(1)
        self.assertEqual(self.client.post("/auth/team/verification", json={"token": recovery}).json()["purpose"], "recover")
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": recovery, "auth_type": "otp"}).status_code, 200)
        self.assertEqual(self.client.get("/auth/team/me").status_code, 401)
        self.assertEqual(self.client.post("/auth/team/activate", json={"token": recovery, "auth_type": "otp"}).status_code, 400)
        self.assertEqual(self.client.post("/auth/team/password", json={"email": payload["email"], "password": self.password}).status_code, 401)

    @patch("teams.send_email")
    def test_team_service_and_public_guards(self, mail):
        payload = self.prepare_team()
        self.connection.execute("UPDATE zeptomail_integration SET enabled = FALSE WHERE id = 1")
        self.assertEqual(self.client.post("/company/teams/invite", json=payload).status_code, 503)
        self.assertEqual(self.client.post("/auth/team/otp/request", json={"email": payload["email"]}).status_code, 503)
        mail.assert_not_called()
        self.connection.execute("UPDATE zeptomail_integration SET enabled = TRUE WHERE id = 1")
        self.assertEqual(self.client.post("/auth/team/otp/request", json={"email": payload["email"]}).status_code, 202)
        self.assertEqual(self.client.post("/auth/team/recover", json={"email": payload["email"]}).status_code, 202)
        mail.assert_not_called()
        for action in ("password", "otp/request", "otp/verify", "recover", "verification", "activate", "logout"):
            result = self.client.post(f"/auth/team/{action}", json={}, headers={"Origin": "https://untrusted.example"})
            self.assertEqual(result.status_code, 403, action)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/auth/team/me").status_code, 401)
        self.assertEqual(self.client.get("/team/people").status_code, 401)
        self.assertEqual(self.client.post("/company/teams/", json={"name": "Unauthorized"}).status_code, 401)

    def test_hashing_and_creation(self):
        self.assertTrue(verify_password(self.password, self.encoded))
        self.assertFalse(verify_password("incorrect", self.encoded))
        self.assertNotEqual(hash_password(self.password), self.encoded)
        with self.assertRaises(ValueError):
            hash_password("short")
        email = f"creator-{secrets.token_hex(8)}@example.com"
        admin_id = create_admin(self.connection, email.upper(), " Super Admin ", self.password)
        admin = self.connection.execute("SELECT * FROM admin WHERE id = %s", (admin_id,)).fetchone()
        self.assertEqual(admin["email"], email)
        self.assertEqual(admin["role"], "super_admin")
        self.assertNotEqual(admin["password_hash"], self.password)
        self.assertTrue(verify_password(self.password, admin["password_hash"]))
        with self.assertRaises(UniqueViolation), self.connection.transaction():
            create_admin(self.connection, email, "Duplicate", self.password)

    def test_session_and_logout(self):
        self.assertEqual(self.client.get("/auth/admin/me").status_code, 401)
        response = self.login(email=self.email.upper())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password_hash", response.json())
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assert_session_lifetime(response, "mikan_admin_session")
        token = self.client.cookies.get("mikan_admin_session")
        stored = self.connection.execute("SELECT token_hash FROM admin_session WHERE admin_id = %s", (self.admin_id,)).fetchone()
        self.assertEqual(stored["token_hash"], hash_token(token))
        self.assertEqual(self.client.get("/auth/admin/me").json()["id"], self.admin_id)
        self.assertEqual(self.client.post("/auth/admin/logout").status_code, 200)
        self.client.cookies.set("mikan_admin_session", token)
        self.assertEqual(self.client.get("/auth/admin/me").status_code, 401)

    def test_invalid_disabled_and_throttled(self):
        unknown = self.login(email=f"absent-{secrets.token_hex(8)}@example.com")
        invalid = self.login(password="incorrect")
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(unknown.json(), invalid.json())
        self.connection.execute("UPDATE admin SET is_active = FALSE WHERE id = %s", (self.admin_id,))
        self.assertEqual(self.login().status_code, 401)
        self.connection.execute("UPDATE admin_login_attempt SET failures = 5 WHERE email = %s", (self.email,))
        self.assertEqual(self.login().status_code, 429)

    def test_expired_and_disabled_sessions(self):
        self.assertEqual(self.login().status_code, 200)
        self.connection.execute("UPDATE admin SET is_active = FALSE WHERE id = %s", (self.admin_id,))
        self.assertEqual(self.client.get("/auth/admin/me").status_code, 401)
        self.connection.execute("UPDATE admin SET is_active = TRUE WHERE id = %s", (self.admin_id,))
        self.connection.execute("UPDATE admin_session SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE admin_id = %s", (self.admin_id,))
        self.assertEqual(self.client.get("/auth/admin/me").status_code, 401)

    def test_csrf_and_validation(self):
        response = self.client.post("/auth/admin/login", headers={"Origin": "https://untrusted.example"}, json={"email": self.email, "password": self.password})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/auth/admin/logout", headers={"Origin": "https://untrusted.example"}).status_code, 403)
        invalid = self.login(password="x" * 129)
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn("x" * 129, invalid.text)

    def company_payload(self):
        output = io.BytesIO()
        Image.new("RGB", (32, 32), "green").save(output, format="PNG")
        return {"name": self.email, "mobile": "+91 98765 43210", "email": self.email,
                "website": "https://example.com", "address": "12 Test Road, Nagercoil",
                "logo_base64": base64.b64encode(output.getvalue()).decode()}

    def test_company_admin_authentication(self):
        root = "/auth/company/admin"
        self.assertEqual(self.client.get(f"{root}/me").status_code, 401)
        self.login()
        super_token = self.client.cookies.get("mikan_admin_session")
        company = self.client.post("/companies/", json=self.company_payload()).json()
        email = f"company-login-{secrets.token_hex(8)}@example.com"
        account = self.client.post("/company-admins/", json={
            "name": "Company Admin", "email": email, "mobile": "+91 98765 43210",
            "password": self.password, "company_id": company["id"],
        }).json()
        credentials = {"email": email.upper(), "password": self.password}
        self.assertEqual(self.client.post(f"{root}/login", json={**credentials, "email": self.email}).status_code, 401)
        self.assertEqual(self.client.post(f"{root}/login", json=credentials, headers={"Origin": "https://untrusted.example"}).status_code, 403)
        self.assertEqual(self.client.post(f"{root}/logout", headers={"Origin": "https://untrusted.example"}).status_code, 403)
        self.assertEqual(self.client.post(f"{root}/login", json={**credentials, "password": "wrong"}).status_code, 401)
        with patch("zeptomail.httpx.Client") as provider:
            response = self.client.post(f"{root}/login", json=credentials)
            provider.assert_not_called()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assert_session_lifetime(response, "mikan_company_admin_session")
        token = self.client.cookies.get("mikan_company_admin_session")
        self.assertEqual(self.client.cookies.get("mikan_admin_session"), super_token)
        identity = self.client.get(f"{root}/me", params={"company_id": 999999}).json()
        self.assertEqual(identity["company_id"], company["id"])
        self.assertEqual(identity["company_name"], company["name"])
        self.assertNotIn("password", str(identity))
        self.assertEqual(self.client.get("/auth/admin/me").status_code, 200)
        self.client.cookies.clear()
        self.client.cookies.set("mikan_admin_session", token)
        self.client.cookies.set("mikan_company_admin_session", super_token)
        self.assertEqual(self.client.get("/auth/admin/me").status_code, 401)
        self.assertEqual(self.client.get("/companies/").status_code, 401)
        self.assertEqual(self.client.get(f"{root}/me").status_code, 401)
        self.client.cookies.set("mikan_company_admin_session", token)
        self.connection.execute("UPDATE admin SET is_active = FALSE WHERE id = %s", (account["id"],))
        self.assertEqual(self.client.get(f"{root}/me").status_code, 401)
        self.assertEqual(self.client.post(f"{root}/login", json=credentials).status_code, 401)
        self.connection.execute("UPDATE admin SET is_active = TRUE WHERE id = %s", (account["id"],))
        self.connection.execute("UPDATE admin_session SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE token_hash = %s", (hash_token(token),))
        self.assertEqual(self.client.get(f"{root}/me").status_code, 401)
        self.client.cookies.clear()
        self.assertEqual(self.client.post(f"{root}/login", json=credentials).status_code, 200)
        token = self.client.cookies.get("mikan_company_admin_session")
        self.assertEqual(self.client.post(f"{root}/logout").status_code, 200)
        self.client.cookies.set("mikan_company_admin_session", token)
        self.assertEqual(self.client.get(f"{root}/me").status_code, 401)
        self.connection.execute("INSERT INTO admin_login_attempt (email, failures) VALUES (%s, 5)", (email,))
        self.assertEqual(self.client.post(f"{root}/login", json=credentials).status_code, 429)

    def test_own_company_profile(self):
        root = "/company/profile"
        payload = self.company_payload()
        self.assertEqual(self.client.get(root).status_code, 401)
        self.assertEqual(self.client.put(root, json=payload).status_code, 401)
        self.assertEqual(self.client.get(root + "/logo").status_code, 401)
        self.login()
        super_token = self.client.cookies.get("mikan_admin_session")
        company = self.client.post("/companies/", json=payload).json()
        other = self.client.post("/companies/", json={**payload, "name": "Other company"}).json()
        self.assertEqual(self.client.get(root).status_code, 401)
        email = f"profile-{secrets.token_hex(8)}@example.com"
        account = self.client.post("/company-admins/", json={
            "name": "Company editor", "email": email, "mobile": "+91 98765 43210",
            "password": self.password, "company_id": company["id"],
        }).json()
        self.client.cookies.clear()
        self.client.cookies.set("mikan_company_admin_session", super_token)
        self.assertEqual(self.client.get(root).status_code, 401)
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/auth/company/admin/login", json={"email": email, "password": self.password}).status_code, 200)
        self.assertEqual(self.client.get(root, params={"company_id": other["id"]}).json()["id"], company["id"])
        self.assertEqual(self.client.get(f"/companies/{other['id']}").status_code, 401)
        self.assertEqual(self.client.put(f"/companies/{other['id']}", json=payload).status_code, 401)
        self.assertEqual(self.client.get(f"/companies/{other['id']}/logo").status_code, 401)
        self.assertEqual(self.client.put(root, json={**payload, "company_id": other["id"]}).status_code, 422)
        self.assertEqual(self.client.put(root, json=payload, headers={"Origin": "https://other.example"}).status_code, 403)
        original_logo = self.client.get(root + "/logo").content
        changed = {**payload, "name": "Updated company", "mobile": "+91 91234 56789", "address": "New address", "website": "https://example.org", "logo_base64": None}
        response = self.client.put(root, json=changed, params={"company_id": other["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], company["id"])
        self.assertEqual(self.client.get(root).json()["address"], "New address")
        self.assertEqual(self.client.get(root + "/logo").content, original_logo)
        self.assertEqual(self.client.get("/auth/company/admin/me").json()["company_name"], "Updated company")
        invalid = self.client.put(root, json={**changed, "name": "Must not save", "logo_base64": "bad"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.get(root).json()["name"], "Updated company")
        output = io.BytesIO()
        Image.new("RGB", (16, 16), "red").save(output, format="JPEG")
        self.assertEqual(self.client.put(root, json={**changed, "logo_base64": base64.b64encode(output.getvalue()).decode()}).status_code, 200)
        logo = self.client.get(root + "/logo")
        self.assertEqual(logo.headers["content-type"], "image/webp")
        self.assertIn("no-store", logo.headers["cache-control"])
        self.assertNotEqual(logo.content, original_logo)
        self.assertEqual(self.client.put(root, json={**changed, "remove_logo": True}).status_code, 200)
        self.assertFalse(self.client.get(root).json()["has_logo"])
        self.assertEqual(self.client.get(root + "/logo").status_code, 404)
        self.assertEqual(self.connection.execute("SELECT name FROM company WHERE id = %s", (other["id"],)).fetchone()["name"], "Other company")
        self.connection.execute("UPDATE admin SET is_active = FALSE WHERE id = %s", (account["id"],))
        self.assertEqual(self.client.get(root).status_code, 401)
        self.assertEqual(self.client.put(root, json=changed).status_code, 401)
        self.assertEqual(self.client.get(root + "/logo").status_code, 401)

    def test_company_admin_creation(self):
        self.login()
        company = self.client.post("/companies/", json=self.company_payload()).json()
        email = f"company-admin-{secrets.token_hex(8)}@example.com"
        payload = {"name": " Company Admin ", "email": email.upper(), "mobile": "+91 98765 43210",
                   "password": self.password, "company_id": company["id"]}
        with patch("zeptomail.httpx.Client") as provider:
            response = self.client.post("/company-admins/", json=payload)
            provider.assert_not_called()
        self.assertEqual(response.status_code, 201, response.text)
        created = response.json()
        self.assertEqual(created["name"], "Company Admin")
        self.assertEqual(created["email"], email)
        self.assertEqual(created["company_id"], company["id"])
        self.assertEqual(created["company_name"], company["name"])
        self.assertEqual(created["role"], "admin")
        self.assertEqual(created["mobile"], payload["mobile"])
        self.assertNotIn("password", response.text)
        self.assertNotIn(self.password, response.text)
        stored = self.connection.execute("SELECT password_hash FROM admin WHERE id = %s", (created["id"],)).fetchone()
        self.assertTrue(verify_password(self.password, stored["password_hash"]))
        self.assertNotEqual(stored["password_hash"], self.password)
        self.assertEqual(self.client.post("/company-admins/", json=payload).status_code, 409)
        self.assertEqual(self.client.post("/company-admins/", json={**payload, "email": self.email}).status_code, 409)
        listing = self.client.get("/company-admins/", params={"search": email, "page_size": 1})
        self.assertEqual(listing.json()["total"], 1)
        self.assertEqual(listing.json()["items"][0]["id"], created["id"])
        self.assertNotIn("password", listing.text)
        self.assertEqual(self.client.get("/company-admins/", params={"search": email, "page_size": 1, "page": 2}).json()["items"], [])
        self.assertEqual(self.client.get("/company-admins/", params={"search": email, "to_date": "2000-01-01"}).json()["total"], 0)
        self.assertEqual(self.client.get("/company-admins/", params={"search": self.email}).json()["total"], 1)
        self.client.cookies.clear()
        self.assertEqual(self.login(email=email).status_code, 401)
        token = secrets.token_urlsafe(32)
        self.connection.execute(
            "INSERT INTO admin_session (token_hash, admin_id, expires_at) VALUES (%s, %s, CURRENT_TIMESTAMP + INTERVAL '1 hour')",
            (hash_token(token), created["id"]),
        )
        self.client.cookies.set("mikan_admin_session", token)
        for path in ["/auth/admin/me", "/company-admins/", "/companies/", "/integrations/zeptomail"]:
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/company-admins/", json=payload).status_code, 401)

    def test_company_admin_validation(self):
        payload = {"name": "Test Admin", "email": self.email, "mobile": "+91 98765 43210",
                   "password": self.password, "company_id": 1}
        self.assertEqual(self.client.post("/company-admins/", json=payload).status_code, 401)
        self.assertEqual(self.client.get("/company-admins/").status_code, 401)
        self.login()
        self.assertEqual(self.client.post("/company-admins/", json=payload, headers={"Origin": "https://other.example"}).status_code, 403)
        for field, value in [("name", " "), ("email", "bad"), ("mobile", "invalid"), ("password", "short"),
                             ("password", "x" * 129), ("company_id", 0), ("company_id", True), ("company_id", 9223372036854775807), ("role", "super_admin")]:
            with self.subTest(field=field):
                response = self.client.post("/company-admins/", json={**payload, field: value})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn(self.password, response.text)
        self.assertEqual(self.client.get("/company-admins/?page=0").status_code, 422)
        self.assertEqual(self.client.get("/company-admins/?from_date=2026-10-05&to_date=2026-10-01").status_code, 422)

    def test_zeptomail_settings_security(self):
        path = "/integrations/zeptomail"
        payload = {"endpoint": "https://cpaas.zoho.com/v1.1/email", "sender_email": self.email,
                   "sender_name": "Mikan", "token": "private-test-token", "enabled": True}
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.put(path, json=payload).status_code, 401)
        self.assertEqual(self.client.post(path + "/test", json={"recipient": self.email}).status_code, 401)
        self.assertEqual(self.client.delete(path).status_code, 401)
        self.login()
        self.connection.execute("DELETE FROM zeptomail_integration")
        self.assertFalse(self.client.get(path).json()["configured"])
        self.assertEqual(self.client.put(path, json=payload, headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.delete(path, headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.put(path, json={**payload, "endpoint": "http://127.0.0.1/email"}).status_code, 422)
        invalid = self.client.put(path, json={**payload, "token": "secret\r\nheader"})
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn("secret", invalid.text)
        self.assertEqual(self.client.put(path, json={**payload, "token": None}).status_code, 422)
        with patch.dict("os.environ", {"INTEGRATION_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            saved = self.client.put(path, json=payload)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertTrue(saved.json()["configured"])
            self.assertNotIn(payload["token"], saved.text)
            encrypted = self.connection.execute("SELECT token_encrypted FROM zeptomail_integration").fetchone()["token_encrypted"]
            self.assertNotIn(payload["token"], encrypted)
            self.assertEqual(self.client.put(path, json={**payload, "token": None, "sender_name": "Updated"}).status_code, 200)
            self.assertEqual(self.connection.execute("SELECT token_encrypted FROM zeptomail_integration").fetchone()["token_encrypted"], encrypted)
            with patch("zeptomail.httpx.Client") as provider:
                send = provider.return_value.__enter__.return_value.post
                send.return_value = httpx.Response(200, json={"message": "OK"}, request=httpx.Request("POST", payload["endpoint"]))
                response = self.client.post(path + "/test", json={"recipient": self.email})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "accepted")
                self.assertEqual(send.call_args.kwargs["headers"]["Authorization"], "Zoho-enczapikey private-test-token")
                self.assertEqual(send.call_args.kwargs["json"]["from"]["name"], "Updated")
                self.assertEqual(self.client.post(path + "/test", json={"recipient": self.email}).status_code, 429)
            self.connection.execute("UPDATE zeptomail_integration SET last_test_at = NULL")
            with patch("zeptomail.httpx.Client") as provider:
                provider.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError("private-provider-error")
                response = self.client.post(path + "/test", json={"recipient": self.email})
                self.assertEqual(response.status_code, 502)
                self.assertNotIn("private-provider-error", response.text)
                self.assertEqual(self.client.get(path).json()["settings"]["last_test_status"], "failed")
            self.assertFalse(self.client.delete(path).json()["configured"])

    def test_company_access_and_validation(self):
        payload = self.company_payload()
        self.assertEqual(self.client.get("/companies/").status_code, 401)
        self.assertEqual(self.client.post("/companies/", json=payload).status_code, 401)
        self.assertEqual(self.client.get("/companies/1/logo").status_code, 401)
        self.assertEqual(self.client.put("/companies/1", json=payload).status_code, 401)
        self.login()
        self.assertEqual(self.client.post("/companies/", json=payload, headers={"Origin": "https://other.example"}).status_code, 403)
        for field, value in [("name", " "), ("email", "bad"), ("website", "javascript:alert(1)"),
                             ("mobile", "invalid"), ("address", " "), ("logo_base64", "bm90IGFuIGltYWdl"),
                             ("logo_base64", "x" * 2796205)]:
            with self.subTest(field=field, length=len(value)):
                self.assertEqual(self.client.post("/companies/", json={**payload, field: value}).status_code, 422)
        self.assertEqual(self.client.get("/companies/?page=0").status_code, 422)
        self.assertEqual(self.client.get("/companies/?from_date=2026-10-05&to_date=2026-10-01").status_code, 422)
        self.assertEqual(self.connection.execute("SELECT count(*) AS total FROM company WHERE name = %s", (self.email,)).fetchone()["total"], 0)

    def test_company_create_edit_and_logo(self):
        self.login()
        payload = self.company_payload()
        created = self.client.post("/companies/", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        company = created.json()
        path = f"/companies/{company['id']}"
        self.assertTrue(company["has_logo"])
        self.assertNotIn("logo_data", company)
        self.assertEqual(self.client.get(path).json()["address"], payload["address"])
        logo = self.client.get(path + "/logo")
        self.assertEqual(logo.headers["content-type"], "image/webp")
        self.assertEqual(logo.headers["x-content-type-options"], "nosniff")
        with Image.open(io.BytesIO(logo.content)) as image:
            self.assertEqual(image.format, "WEBP")
        del payload["logo_base64"]
        payload["address"] = "Updated address"
        updated = self.client.put(path, json=payload)
        self.assertEqual(updated.status_code, 200)
        self.assertTrue(updated.json()["has_logo"])
        self.assertEqual(updated.json()["address"], "Updated address")
        self.assertEqual(self.client.put(path, json={**payload, "remove_logo": True}).json()["has_logo"], False)
        self.assertEqual(self.client.get(path + "/logo").status_code, 404)
        self.assertEqual(self.client.put("/companies/-1", json=payload).status_code, 404)
        self.connection.execute("UPDATE admin SET is_active = FALSE WHERE id = %s", (self.admin_id,))
        self.assertEqual(self.client.get(path).status_code, 401)

    def test_jpeg_logo_and_specific_rejections(self):
        self.login()
        payload = self.company_payload()
        for image_format in ("JPEG", "PNG", "WEBP"):
            with self.subTest(image_format=image_format):
                output = io.BytesIO()
                Image.new("RGB", (96, 48), "green").save(output, format=image_format)
                payload["logo_base64"] = base64.b64encode(output.getvalue()).decode()
                created = self.client.post("/companies/", json=payload)
                self.assertEqual(created.status_code, 201, created.text)
                self.assertTrue(created.json()["has_logo"])
        output = io.BytesIO()
        Image.new("RGB", (8193, 1), "green").save(output, format="JPEG")
        payload["logo_base64"] = base64.b64encode(output.getvalue()).decode()
        rejected = self.client.post("/companies/", json=payload)
        self.assertEqual(rejected.status_code, 422)
        self.assertIn("8193 x 1", rejected.json()["detail"])
        self.assertIn("Resize", rejected.json()["detail"])
        payload["logo_base64"] = base64.b64encode(b"invalid jpeg").decode()
        self.assertIn("could not be decoded", self.client.post("/companies/", json=payload).json()["detail"])

    def test_company_search_dates_and_pagination(self):
        self.login()
        payload = self.company_payload()
        for suffix in (" First", " Second", " Third"):
            self.client.post("/companies/", json={**payload, "name": self.email + suffix})
        result = self.client.get("/companies/", params={"search": self.email, "page_size": 2}).json()
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["items"]), 2)
        second = self.client.get("/companies/", params={"search": self.email, "page_size": 2, "page": 2}).json()
        self.assertEqual(len(second["items"]), 1)
        self.assertNotIn(second["items"][0]["id"], [item["id"] for item in result["items"]])
        empty = self.client.get("/companies/", params={"search": self.email, "to_date": "2000-01-01"}).json()
        self.assertEqual(empty["total"], 0)


if __name__ == "__main__":
    unittest.main()