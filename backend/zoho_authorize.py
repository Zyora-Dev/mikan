import argparse
import getpass
import io
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import warnings

from dotenv import dotenv_values, set_key
from dotenv.parser import parse_stream
import httpx


CREDENTIALS_PATH = Path(__file__).with_name('.env.zoho')
ACCOUNTS_URL = 'https://accounts.zoho.in'
SCOPES = 'WorkDrive.teamfolders.READ,WorkDrive.files.READ,ZohoFiles.files.READ'


class AuthorizationError(Exception):
    pass


def load_credentials(path, *, require_refresh=False):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise AuthorizationError('Credentials must be a regular file owned by your user, not a symlink.')
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise AuthorizationError('Credentials file must have owner-only permissions (chmod 600).')
    original = path.read_bytes()
    text = original.decode('utf-8')
    bindings = list(parse_stream(io.StringIO(text)))
    keys = [binding.key for binding in bindings if binding.key is not None]
    if any(binding.error for binding in bindings) or len(keys) != len(set(keys)):
        raise AuthorizationError('Credentials file has invalid syntax or duplicate keys; fix it privately in the editor.')
    values = dotenv_values(stream=io.StringIO(text), interpolate=False)
    for key in ('ZOHO_CLIENT_ID', 'ZOHO_CLIENT_SECRET'):
        if not (values.get(key) or '').strip():
            raise AuthorizationError(f'{key} is missing; enter it privately in backend/.env.zoho.')
    if values.get('ZOHO_ACCOUNTS_URL') != ACCOUNTS_URL:
        raise AuthorizationError('ZOHO_ACCOUNTS_URL must be https://accounts.zoho.in for this India Self Client.')
    if require_refresh and not (values.get('ZOHO_REFRESH_TOKEN') or '').strip():
        raise AuthorizationError('A saved refresh token is required. Complete Self Client authorization first.')
    if not require_refresh and values.get('ZOHO_REFRESH_TOKEN'):
        raise AuthorizationError('A refresh token is already saved. It will not be overwritten.')
    return values, original


def exchange_code(values, code):
    if not code or any(character.isspace() for character in code):
        raise AuthorizationError('Authorization code is empty or contains whitespace.')
    try:
        response = httpx.post(
            f'{ACCOUNTS_URL}/oauth/v2/token',
            data={
                'client_id': values['ZOHO_CLIENT_ID'],
                'client_secret': values['ZOHO_CLIENT_SECRET'],
                'grant_type': 'authorization_code',
                'code': code,
            },
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        )
    except httpx.RequestError:
        raise AuthorizationError('Token request failed. No automatic retry was made; the code may have been consumed.') from None
    if response.status_code != 200:
        raise AuthorizationError('Zoho rejected the token request. No token was saved; response details are hidden.')
    try:
        payload = response.json()
    except ValueError:
        raise AuthorizationError('Zoho returned an invalid response. No token was saved.') from None
    if not isinstance(payload, dict):
        raise AuthorizationError('Zoho returned an unexpected response. No token was saved.')
    if payload.get('error'):
        message = {
            'invalid_code': 'The code is expired, already used, or invalid. Generate a new code before another attempt.',
            'invalid_client': 'Zoho rejected the client credentials. Check them privately in the editor.',
            'invalid_client_secret': 'Zoho rejected the client secret. Check it privately in the editor.',
        }.get(str(payload['error']), 'Zoho rejected authorization. No token was saved; response details are hidden.')
        raise AuthorizationError(message)
    token = payload.get('refresh_token')
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9._~-]+', token):
        raise AuthorizationError('Zoho did not return a valid refresh token. Existing credentials were not changed.')
    return token


def save_refresh_token(path, original, token):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.env.zoho.', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(original)
        set_key(temporary, 'ZOHO_REFRESH_TOKEN', token, quote_mode='always')
        temporary.chmod(0o600)
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        _, current = load_credentials(path)
        if current != original:
            raise AuthorizationError('Credentials changed during authorization. Refusing to overwrite your edits.')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Authorize a Zoho India Self Client without displaying secrets.')
    parser.add_argument('--check', action='store_true', help='Validate local credentials only; no network requests or writes.')
    args = parser.parse_args(argv)
    try:
        values, original = load_credentials(CREDENTIALS_PATH)
        if args.check:
            print('Ready: client credentials present, India endpoint selected, file permissions 600.')
            print('No network request or file change was made.')
            return 0
        if not sys.stdin.isatty():
            raise AuthorizationError('Run this helper yourself in an interactive terminal for hidden code entry.')
        print('Ready. In your existing Self Client, generate a code using these scopes:')
        print(SCOPES)
        print('Paste the code at the hidden prompt below, then press Enter. Do not share it in chat.')
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            code = getpass.getpass('Authorization code (hidden): ').strip()
        token = exchange_code(values, code)
        save_refresh_token(CREDENTIALS_PATH, original, token)
        print('Refresh token saved privately in backend/.env.zoho (permissions 600).')
        print('No files were migrated and no production settings were changed.')
        return 0
    except AuthorizationError as error:
        print(f'Authorization stopped: {error}', file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print('\nCancelled.', file=sys.stderr)
    except getpass.GetPassWarning:
        print('Hidden input is unavailable. Use an interactive terminal; no request was sent.', file=sys.stderr)
    except (OSError, UnicodeError):
        print('Could not read or save the private credentials file. Details are hidden to protect secrets.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())