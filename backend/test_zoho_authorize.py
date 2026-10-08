import contextlib
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

import zoho_authorize as authorize


class ZohoAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '.env.zoho'
        self.original = (
            '# Private configuration\n'
            'ZOHO_CLIENT_ID=test-client\n'
            'ZOHO_CLIENT_SECRET="test-secret"\n'
            'ZOHO_REFRESH_TOKEN=\n'
            'ZOHO_ACCOUNTS_URL=https://accounts.zoho.in\n'
            'UNRELATED="keep ${literal}"\n'
        ).encode()
        self.path.write_bytes(self.original)
        self.path.chmod(0o600)

    def run_main(self, args, response=None):
        output = io.StringIO()
        with (
            patch.object(authorize, 'CREDENTIALS_PATH', self.path),
            patch.object(authorize.sys.stdin, 'isatty', return_value=True),
            patch.object(authorize.getpass, 'getpass', return_value='test-code'),
            patch.object(authorize.httpx, 'post', return_value=response) as post,
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            result = authorize.main(args)
        for secret in ('test-secret', 'test-code', 'private-refresh', 'private-access'):
            self.assertNotIn(secret, output.getvalue())
        return result, post

    def test_check_is_offline_and_read_only(self):
        result, post = self.run_main(['--check'])
        self.assertEqual(result, 0)
        post.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_inventory_loading_requires_token_and_never_changes_file(self):
        with self.assertRaises(authorize.AuthorizationError):
            authorize.load_credentials(self.path, require_refresh=True)
        content = self.original.replace(b'ZOHO_REFRESH_TOKEN=', b'ZOHO_REFRESH_TOKEN=private-refresh')
        self.path.write_bytes(content)
        values, original = authorize.load_credentials(self.path, require_refresh=True)
        self.assertEqual(values['ZOHO_REFRESH_TOKEN'], 'private-refresh')
        self.assertEqual(original, content)
        self.assertEqual(self.path.read_bytes(), content)
        with self.assertRaises(authorize.AuthorizationError):
            authorize.load_credentials(self.path)

    def test_success_saves_refresh_only_and_preserves_other_lines(self):
        result, post = self.run_main([], httpx.Response(200, json={
            'refresh_token': 'private-refresh', 'access_token': 'private-access',
        }))
        self.assertEqual(result, 0)
        self.assertEqual(post.call_args.args, ('https://accounts.zoho.in/oauth/v2/token',))
        self.assertEqual(post.call_args.kwargs['data'], {
            'client_id': 'test-client', 'client_secret': 'test-secret',
            'grant_type': 'authorization_code', 'code': 'test-code',
        })
        self.assertFalse(post.call_args.kwargs['follow_redirects'])
        self.assertFalse(post.call_args.kwargs['trust_env'])
        self.assertEqual(self.path.read_bytes(), self.original.replace(
            b'ZOHO_REFRESH_TOKEN=\n', b"ZOHO_REFRESH_TOKEN='private-refresh'\n",
        ))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_request_encoding_with_mock_transport(self):
        values, _ = authorize.load_credentials(self.path)
        def handler(request):
            self.assertEqual(request.method, 'POST')
            self.assertEqual(str(request.url), 'https://accounts.zoho.in/oauth/v2/token')
            self.assertEqual(parse_qs(request.content.decode())['code'], ['code+with&symbols'])
            return httpx.Response(200, json={'refresh_token': 'private-refresh'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch.object(authorize.httpx, 'post', side_effect=lambda url, **kwargs: client.post(
                url, data=kwargs['data'], timeout=kwargs['timeout'], follow_redirects=kwargs['follow_redirects'],
            )):
                self.assertEqual(authorize.exchange_code(values, 'code+with&symbols'), 'private-refresh')

    def test_bad_responses_preserve_file_and_hide_payload(self):
        for response in (
            httpx.Response(200, json={'error': 'invalid_code', 'detail': 'test-secret'}),
            httpx.Response(200, json={'error': 'test-secret'}),
            httpx.Response(200, json={'access_token': 'private-access'}),
            httpx.Response(200, json={'refresh_token': 'bad\nTOKEN=value'}),
            httpx.Response(200, json=[]),
            httpx.Response(200, text='test-secret'),
            httpx.Response(302, headers={'location': 'https://example.com'}),
            httpx.Response(400, text='test-secret'),
        ):
            with self.subTest(status=response.status_code):
                result, post = self.run_main([], response)
                self.assertEqual(result, 1)
                post.assert_called_once()
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_network_error_is_sanitized_and_not_retried(self):
        values, _ = authorize.load_credentials(self.path)
        with patch.object(authorize.httpx, 'post', side_effect=httpx.ConnectError('test-secret')) as post:
            with self.assertRaises(authorize.AuthorizationError) as raised:
                authorize.exchange_code(values, 'test-code')
        self.assertNotIn('test-secret', str(raised.exception))
        post.assert_called_once()

    def test_rejects_unsafe_configuration_before_request(self):
        for old, new in (
            (b'ZOHO_REFRESH_TOKEN=', b'ZOHO_REFRESH_TOKEN=existing'),
            (b'https://accounts.zoho.in', b'https://example.com'),
            (b'ZOHO_CLIENT_ID=test-client', b'ZOHO_CLIENT_ID='),
            (b'ZOHO_CLIENT_ID=test-client', b'ZOHO_CLIENT_ID'),
            (b'ZOHO_CLIENT_ID=test-client', b'ZOHO_CLIENT_ID=one\nZOHO_CLIENT_ID=two'),
            (b'ZOHO_CLIENT_ID=test-client', b'ZOHO_CLIENT_ID="unclosed'),
        ):
            self.path.write_bytes(self.original.replace(old, new))
            result, post = self.run_main([])
            self.assertEqual(result, 1)
            post.assert_not_called()

    def test_rejects_public_permissions_and_symlinks(self):
        self.path.chmod(0o644)
        with self.assertRaises(authorize.AuthorizationError):
            authorize.load_credentials(self.path)
        self.path.chmod(0o600)
        link = self.path.parent / 'link'
        link.symlink_to(self.path)
        with self.assertRaises(authorize.AuthorizationError):
            authorize.load_credentials(link)

    def test_save_preserves_concurrent_edit_and_cleans_temporary_file(self):
        edited = self.original + b'NEW_SETTING=keep\n'
        self.path.write_bytes(edited)
        with self.assertRaises(authorize.AuthorizationError):
            authorize.save_refresh_token(self.path, self.original, 'private-refresh')
        self.assertEqual(self.path.read_bytes(), edited)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_failed_replace_preserves_original(self):
        with patch.object(authorize.os, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                authorize.save_refresh_token(self.path, self.original, 'private-refresh')
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_noninteractive_input_does_not_request_token(self):
        with patch.object(authorize.sys.stdin, 'isatty', return_value=False), patch.object(
            authorize, 'CREDENTIALS_PATH', self.path,
        ), patch.object(authorize.httpx, 'post') as post, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(authorize.main([]), 1)
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()