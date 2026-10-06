from contextlib import ExitStack, closing
from datetime import date
import re
from typing import Literal
from urllib.parse import quote
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field, StrictInt
from starlette.background import BackgroundTask

from database import connect, get_db
from storage import StorageInput, destination
from teams import current_team_account, email_settings, public_origin
from zeptomail import cipher, send_email
from workflows import REVIEW_ACCESS_SQL, StrictInput, date_clause

SHARE_ACCESS_SQL = """EXISTS (
    SELECT 1 FROM file_share share JOIN team_account owner ON owner.id=file.owner_id
    WHERE share.file_id=file.id AND share.company_id=file.company_id AND share.recipient_id=%s
      AND owner.company_id=file.company_id AND owner.team_id=file.team_id AND owner.status='active'
)"""


class FilePermissionInput(StrictInput):
    permission: Literal['view', 'edit'] = 'view'


class FileShareInput(FilePermissionInput):
    recipient_ids: list[StrictInt] = Field(min_length=1, max_length=50)


class FileLinkInput(StrictInput):
    public_access: bool = Field(strict=True)


def record_file_activity(connection, account, file_id, action, detail):
    connection.execute('INSERT INTO file_activity(file_id,company_id,actor,action,detail) VALUES (%s,%s,%s,%s,%s)',
        (file_id, account['company_id'], account['name'], action, detail))


def create_file_share_router(origin_dependency):
    sharing = APIRouter(prefix='/team/files')
    mutation = [Depends(origin_dependency)]

    def owned_file(connection, account, file_id):
        record = connection.execute("""SELECT file.id,file.state,file.share_token,file.public_access,storage.enabled FROM stored_file file
            JOIN storage_connection storage ON storage.id=file.connection_id
            JOIN team_account owner ON owner.id=file.owner_id AND owner.company_id=file.company_id
                AND owner.team_id=file.team_id AND owner.status='active'
            WHERE file.id=%s AND file.company_id=%s AND file.team_id=%s AND file.owner_id=%s
            FOR UPDATE OF file FOR SHARE OF owner,storage""",
            (file_id, account['company_id'], account['team_id'], account['id'])).fetchone()
        if not record:
            raise HTTPException(404, 'File not found.')
        return record

    @sharing.get('/{file_id}/link')
    def file_link(file_id: UUID, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        record = owned_file(connection, account, file_id)
        return {'url': f"{public_origin()}/share/{record['share_token']}", 'public_access': record['public_access']}

    @sharing.post('/{file_id}/link', dependencies=mutation)
    def update_link(file_id: UUID, payload: FileLinkInput, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        with connection.transaction():
            record = owned_file(connection, account, file_id)
            if payload.public_access and (record['state'] != 'ready' or not record['enabled']):
                raise HTTPException(409, 'Only available uploaded files can be shared publicly.')
            connection.execute('UPDATE stored_file SET public_access=%s WHERE id=%s', (payload.public_access, file_id))
            if payload.public_access:
                connection.execute("UPDATE file_share SET permission='view' WHERE file_id=%s AND permission='edit'", (file_id,))
            if payload.public_access != record['public_access']:
                record_file_activity(connection, account, file_id, 'link_access', 'Public view-only link; Edit grants changed to View.' if payload.public_access else 'Link access restricted.')
        return {'detail': 'Link access updated.'}

    @sharing.get('/shared')
    def shared_files(connection=Depends(get_db, scope="function"), account=Depends(current_team_account),
                     page: int = Query(1, ge=1, le=100000), search: str = Query('', max_length=100),
                     from_date: date | None = None, to_date: date | None = None):
        dates, date_values = date_clause(from_date, to_date, 'file.created_at')
        source = 'stored_file file JOIN storage_connection storage ON storage.id=file.connection_id JOIN team_account owner ON owner.id=file.owner_id'
        where = f"file.company_id=%s AND {SHARE_ACCESS_SQL} AND file.state='ready' AND storage.enabled AND strpos(lower(file.name),lower(%s))>0 AND {dates}"
        values = [account['company_id'], account['id'], search, *date_values]
        total = connection.execute(f'SELECT count(*) AS total FROM {source} WHERE {where}', values).fetchone()['total']
        items = connection.execute(f"""SELECT 'file' AS kind,file.id,file.name,'' AS folder,file.size_bytes,
            file.state,file.created_at,owner.name AS shared_by FROM {source} WHERE {where}
            ORDER BY file.created_at DESC,file.id DESC LIMIT 10 OFFSET %s""", [*values, (page - 1) * 10]).fetchall()
        return {'items': items, 'total': total, 'policy': None}

    @sharing.get('/{file_id}/share-members')
    def share_members(file_id: UUID, connection=Depends(get_db, scope="function"), account=Depends(current_team_account),
                      search: str = Query('', max_length=100)):
        owned_file(connection, account, file_id)
        items = connection.execute("""SELECT person.id,person.name,person.email,team.name AS team_name
            FROM team_account person JOIN team ON team.id=person.team_id AND team.company_id=person.company_id
            WHERE person.company_id=%s AND person.status='active' AND person.id<>%s
              AND (strpos(lower(person.name),lower(%s))>0 OR strpos(lower(person.email),lower(%s))>0)
              AND NOT EXISTS (SELECT 1 FROM file_share WHERE file_id=%s AND recipient_id=person.id)
            ORDER BY lower(person.name),person.id LIMIT 10""",
            (account['company_id'], account['id'], search.strip(), search.strip(), file_id)).fetchall()
        return {'items': items}

    @sharing.get('/{file_id}/shares')
    def file_shares(file_id: UUID, connection=Depends(get_db, scope="function"), account=Depends(current_team_account),
                    page: int = Query(1, ge=1, le=100000)):
        owned_file(connection, account, file_id)
        values = (file_id, account['company_id'])
        total = connection.execute('SELECT count(*) AS total FROM file_share WHERE file_id=%s AND company_id=%s', values).fetchone()['total']
        items = connection.execute("""SELECT person.id,person.name,person.email,person.status,team.name AS team_name,
            share.created_at,share.email_status,share.permission FROM file_share share JOIN team_account person ON person.id=share.recipient_id
            JOIN team ON team.id=person.team_id WHERE share.file_id=%s AND share.company_id=%s
            ORDER BY share.created_at DESC,person.id DESC LIMIT 10 OFFSET %s""", (*values, (page - 1) * 10)).fetchall()
        return {'items': items, 'total': total}

    @sharing.post('/{file_id}/shares', dependencies=mutation)
    def add_shares(file_id: UUID, payload: FileShareInput, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        recipients = sorted(set(payload.recipient_ids))
        with connection.transaction():
            record = owned_file(connection, account, file_id)
            if any(identifier <= 0 or identifier > 9223372036854775807 or identifier == account['id'] for identifier in recipients):
                raise HTTPException(422, 'Choose other active company members.')
            if record['state'] != 'ready' or not record['enabled']:
                raise HTTPException(409, 'Only available uploaded files can be shared.')
            if payload.permission == 'edit' and record['public_access']:
                raise HTTPException(409, 'Switch to restricted access before granting Edit.')
            people = connection.execute("""SELECT id FROM team_account WHERE id=ANY(%s)
                AND company_id=%s AND status='active' ORDER BY id FOR SHARE""", (recipients, account['company_id'])).fetchall()
            if len(people) != len(recipients):
                raise HTTPException(422, 'Choose active members of your company.')
            for identifier in recipients:
                added = connection.execute("""INSERT INTO file_share(file_id,company_id,recipient_id,permission) VALUES (%s,%s,%s,%s)
                    ON CONFLICT (file_id,recipient_id) DO NOTHING RETURNING recipient_id""", (file_id, account['company_id'], identifier, payload.permission)).fetchone()
                if added:
                    record_file_activity(connection, account, file_id, 'access_granted', f'Member {identifier}: {payload.permission} access granted.')
        return {'detail': 'File shared. Email notifications queued.'}

    @sharing.post('/{file_id}/shares/{recipient_id}/permission', dependencies=mutation)
    def update_permission(file_id: UUID, payload: FilePermissionInput, recipient_id: int = Path(ge=1, le=9223372036854775807),
                          connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        with connection.transaction():
            record = owned_file(connection, account, file_id)
            if payload.permission == 'edit' and record['public_access']:
                raise HTTPException(409, 'Switch to restricted access before granting Edit.')
            changed = connection.execute('UPDATE file_share SET permission=%s WHERE file_id=%s AND company_id=%s AND recipient_id=%s RETURNING recipient_id',
                (payload.permission, file_id, account['company_id'], recipient_id)).fetchone()
            if not changed:
                raise HTTPException(404, 'Sharing access not found.')
            record_file_activity(connection, account, file_id, 'permission_changed', f'Member {recipient_id}: {payload.permission} access.')
        return {'detail': 'Permission updated.'}

    @sharing.post('/{file_id}/shares/{recipient_id}/retry-email', dependencies=mutation)
    def retry_email(file_id: UUID, recipient_id: int = Path(ge=1, le=9223372036854775807),
                    connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        with connection.transaction():
            owned_file(connection, account, file_id)
            changed = connection.execute("""UPDATE file_share SET email_status='queued',email_attempts=0,email_next_at=clock_timestamp()
                WHERE file_id=%s AND company_id=%s AND recipient_id=%s AND email_status IN ('failed','cancelled')
                RETURNING recipient_id""", (file_id, account['company_id'], recipient_id)).fetchone()
            if not changed:
                raise HTTPException(409, 'Email is not available for retry.')
        return {'detail': 'Email notification queued.'}

    @sharing.post('/{file_id}/shares/{recipient_id}/remove', dependencies=mutation)
    def remove_share(file_id: UUID, recipient_id: int = Path(ge=1, le=9223372036854775807),
                     connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        with connection.transaction():
            owned_file(connection, account, file_id)
            removed = connection.execute('DELETE FROM file_share WHERE file_id=%s AND company_id=%s AND recipient_id=%s RETURNING recipient_id',
                               (file_id, account['company_id'], recipient_id)).fetchone()
            if removed:
                record_file_activity(connection, account, file_id, 'access_removed', f'Member {recipient_id}: access removed.')
        return {'detail': 'Sharing access removed.'}

    return sharing

FOLDERS = '''(SELECT company_id,team_id,owner_id,path,min(created_at) AS created_at FROM (
    SELECT company_id,team_id,owner_id,array_to_string(parts[1:depth],'/') AS path,created_at
    FROM (SELECT company_id,team_id,owner_id,string_to_array(path,'/') AS parts,created_at FROM data_folder
          UNION ALL SELECT company_id,team_id,owner_id,string_to_array(folder,'/'),created_at FROM stored_file WHERE folder<>'' AND state NOT IN ('cancelled','purged')) source
    CROSS JOIN LATERAL generate_series(1,array_length(parts,1)) AS depth
    ) paths GROUP BY company_id,team_id,owner_id,path) folder'''

PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "CDN-Cache-Control": "no-store",
    "Cloudflare-CDN-Cache-Control": "no-store",
    "Vary": "Cookie",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}

router = APIRouter(prefix="/team/files")

PREVIEW_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "avif": "image/avif", "bmp": "image/bmp",
    "mp4": "video/mp4", "m4v": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "pdf": "application/pdf",
}
PREVIEW_CSP = "default-src 'none'; frame-ancestors 'self'"


@router.get('/link/{token}')
@router.get('/link/{token}/content')
@router.get('/link/{token}/preview')
def shared_link(request: Request, response: Response, token: str = Path(pattern=r'^[0-9a-f]{64}$'), connection=Depends(get_db, scope="function")):
    record = connection.execute("""SELECT file.id AS file_id,file.owner_id,file.company_id,file.public_access,
        file.name AS filename,file.size_bytes,file.object_key,file.object_version,file.etag,storage.*
        FROM stored_file file JOIN storage_connection storage ON storage.id=file.connection_id
        JOIN team_account owner ON owner.id=file.owner_id AND owner.company_id=file.company_id
            AND owner.team_id=file.team_id AND owner.status='active'
        WHERE file.share_token=%s AND file.state='ready' AND storage.enabled""", (token,)).fetchone()
    if not record:
        raise HTTPException(404, 'File not found.')
    if not record['public_access']:
        account = current_team_account(request, response, connection)
        granted = account['company_id'] == record['company_id'] and (account['id'] == record['owner_id'] or
            connection.execute('SELECT 1 FROM file_share WHERE file_id=%s AND company_id=%s AND recipient_id=%s',
                               (record['file_id'], account['company_id'], account['id'])).fetchone())
        if not granted:
            raise HTTPException(403, 'You do not have access to this file.')
    if request.url.path.endswith(('/content', '/preview')):
        preview = request.url.path.endswith('/preview')
        return stream_file(record, preview=preview, byte_range=request.headers.get('range') if preview else None)
    return {'id': record['file_id'], 'name': record['filename'], 'size_bytes': record['size_bytes'], 'state': 'ready', 'restricted_access': not record['public_access']}


def process_share_email(connection):
    job = connection.execute("""SELECT file_id,company_id,recipient_id,email_attempts,permission FROM file_share
        WHERE email_status IN ('queued','retry') AND email_next_at<=clock_timestamp()
        ORDER BY email_next_at,file_id,recipient_id FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
    if not job:
        return False
    values = (job['file_id'], job['company_id'], job['recipient_id'])
    person = connection.execute("""SELECT recipient.email,recipient.name,file.name AS filename,file.share_token,
        owner.name AS sender,owner.email AS sender_email FROM stored_file file
        JOIN storage_connection storage ON storage.id=file.connection_id AND storage.enabled
        JOIN team_account owner ON owner.id=file.owner_id AND owner.company_id=file.company_id
            AND owner.team_id=file.team_id AND owner.status='active'
        JOIN team_account recipient ON recipient.id=%s AND recipient.company_id=file.company_id AND recipient.status='active'
        WHERE file.id=%s AND file.company_id=%s AND file.state='ready'""", (job['recipient_id'], job['file_id'], job['company_id'])).fetchone()
    result = 'cancelled'
    attempts = job['email_attempts'] + 1
    if person:
        try:
            text = (f"Hello {person['name']},\n\n{person['sender']} ({person['sender_email']}) shared a file with you.\n\n"
                    f"File: {person['filename']}\nAccess: {'Edit (upload new versions), view and download' if job['permission'] == 'edit' else 'View and download'}\n\n"
                    f"Open file: {public_origin()}/share/{person['share_token']}\n\n"
                    f"For restricted files, sign in with {person['email']}.\n\nMikan Cloud")
            send_email(email_settings(connection), person['email'], 'A file was shared with you on Mikan', text)
            result = 'accepted'
        except Exception:
            result = 'failed' if attempts >= 5 else 'retry'
    connection.execute("""UPDATE file_share SET email_status=%s,email_attempts=%s,
        email_next_at=clock_timestamp()+(%s * INTERVAL '1 second')
        WHERE file_id=%s AND company_id=%s AND recipient_id=%s""", (result, attempts, min(3600, 30 * 2 ** attempts), *values))
    return True


def deliver_share_emails():
    for attempt in range(10):
        with connect() as connection:
            if not process_share_email(connection):
                break


@router.get("/{file_id}/content")
@router.get("/{file_id}/preview")
def download(file_id: UUID, request: Request, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
    record = connection.execute(
        f"""SELECT file.name AS filename, file.size_bytes, file.object_key, file.object_version,
                  file.etag, storage.* FROM stored_file file
           JOIN storage_connection storage ON storage.id = file.connection_id
              JOIN team ON team.id = file.team_id AND team.company_id = file.company_id
              WHERE file.id = %s AND file.company_id = %s AND (
                  (file.team_id = %s AND (file.owner_id = %s OR (%s = 'manager' AND team.manager_can_view_drives)))
                  OR {REVIEW_ACCESS_SQL} OR {SHARE_ACCESS_SQL})
              AND file.state = 'ready' AND storage.enabled""",
          (file_id, account["company_id"], account["team_id"], account["id"], account["role"], account["id"], account["team_id"], account["id"]),
    ).fetchone()
    if not record:
        raise HTTPException(404, "File not found.")
    preview = request.url.path.endswith("/preview")
    return stream_file(record, preview=preview, byte_range=request.headers.get("range") if preview else None)


def stream_file(record, *, preview=False, byte_range=None):
    media_type = PREVIEW_TYPES.get(record["filename"].rsplit(".", 1)[-1].lower()) if preview else "application/octet-stream"
    if not media_type:
        raise HTTPException(415, "Preview is not available for this file type.")
    size = record["size_bytes"]
    start, end = 0, size - 1
    if byte_range:
        match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", byte_range)
        if not match or not any(match.groups()) or size == 0:
            raise HTTPException(416, "Requested range is not available.", headers={"Content-Range": f"bytes */{size}"})
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:
            start = max(0, size - int(last))
        if start > end or start >= size:
            raise HTTPException(416, "Requested range is not available.", headers={"Content-Range": f"bytes */{size}"})
    length = end - start + 1
    content_range = f"bytes {start}-{end}/{size}"
    resources = ExitStack()
    try:
        _, endpoint = destination(record["provider"], StorageInput(
            name=record["name"], bucket=record["bucket"], region=record["region"],
            account_id=record["account_id"],
            endpoint=record["endpoint"] if record["provider"] == "r2" else None,
        ))
        access = cipher().decrypt(record["access_key_encrypted"].encode()).decode()
        secret = cipher().decrypt(record["secret_key_encrypted"].encode()).decode()
        client = resources.enter_context(closing(boto3.client(
            "s3", endpoint_url=endpoint, region_name=record["region"],
            aws_access_key_id=access, aws_secret_access_key=secret,
            config=Config(signature_version="s3v4", connect_timeout=2, read_timeout=15,
                          retries={"total_max_attempts": 1}, proxies={}, s3={"addressing_style": "path"}),
        )))
        version = {"VersionId": record["object_version"]} if record["provider"] == "s3" and record["object_version"] else {}
        range_options = {"Range": f"bytes={start}-{end}"} if byte_range else {}
        result = client.get_object(Bucket=record["bucket"], Key=record["object_key"], IfMatch=record["etag"], **version, **range_options)
        body = resources.enter_context(closing(result["Body"]))
        if result.get("ContentLength") != length or result.get("ETag") != record["etag"] or (byte_range and result.get("ContentRange") != content_range):
            raise ValueError("Stored object metadata mismatch")
        first_chunk = body.read(64 * 1024)
    except (BotoCoreError, ClientError, InvalidToken, UnicodeError, ValueError, HTTPException) as error:
        resources.close()
        raise HTTPException(503, "File temporarily unavailable.") from error
    except BaseException:
        resources.close()
        raise

    def chunks():
        try:
            yield first_chunk
            while chunk := body.read(64 * 1024):
                yield chunk
        finally:
            resources.close()

    return StreamingResponse(
        chunks(), media_type=media_type, status_code=206 if byte_range else 200,
        headers={**PRIVATE_HEADERS, "Content-Length": str(length),
                 **({"Accept-Ranges": "bytes", "Content-Security-Policy": PREVIEW_CSP} if preview else {}),
                 **({"Content-Range": content_range} if byte_range else {}),
                 "Content-Disposition": ("inline" if preview else "attachment") + "; filename=\"download\"; filename*=UTF-8''" + quote(record["filename"], safe="")},
        background=BackgroundTask(resources.close),
    )