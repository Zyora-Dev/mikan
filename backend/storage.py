import ipaddress
import re
import secrets
from contextlib import closing
from datetime import date
from typing import Literal

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from database import get_db
from zeptomail import cipher

Provider = Literal["r2", "s3"]
PUBLIC_COLUMNS = "id, provider, name, bucket, region, account_id, endpoint, enabled, created_at, updated_at, last_test_at, last_test_status, last_cleanup"


class StorageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    bucket: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
    region: str = Field(default="auto", max_length=40)
    account_id: str | None = Field(default=None, max_length=32)
    endpoint: str | None = Field(default=None, max_length=200)
    access_key: SecretStr | None = Field(default=None, min_length=1, max_length=256)
    secret_key: SecretStr | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool = False

    @field_validator("bucket")
    @classmethod
    def valid_bucket(cls, value):
        if ".." in value or ".-" in value or "-." in value:
            raise ValueError("Invalid bucket name")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return value
        raise ValueError("Bucket must not be an IP address")

    @field_validator("access_key", "secret_key")
    @classmethod
    def valid_secret(cls, value):
        if value is not None:
            raw = value.get_secret_value()
            if not raw.isascii() or any(ord(character) < 33 or ord(character) > 126 for character in raw):
                raise ValueError("Invalid credential")
        return value


class StorageTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]


def destination(provider, payload):
    if provider == "r2":
        account = payload.account_id or ""
        if not re.fullmatch(r"[a-f0-9]{32}", account) or payload.region != "auto":
            raise HTTPException(422, "Enter a valid R2 account ID and use region auto.")
        approved = {f"https://{account}.r2.cloudflarestorage.com", f"https://{account}.eu.r2.cloudflarestorage.com"}
        endpoint = (payload.endpoint or f"https://{account}.r2.cloudflarestorage.com").rstrip("/")
        if endpoint not in approved:
            raise HTTPException(422, "The R2 endpoint must match the account ID and supported Cloudflare domain.")
        return account, endpoint
    if payload.account_id or payload.endpoint:
        raise HTTPException(422, "AWS endpoints are derived from the selected region.")
    regions = boto3.session.Session().get_available_regions("s3")
    if payload.region not in regions:
        raise HTTPException(422, "Select a supported AWS commercial region.")
    return None, f"https://s3.{payload.region}.amazonaws.com"


def save_connection(connection, provider, payload, admin_id, connection_id=None):
    account, endpoint = destination(provider, payload)
    existing = None
    if connection_id is not None:
        existing = connection.execute("SELECT * FROM storage_connection WHERE id = %s AND provider = %s FOR UPDATE", (connection_id, provider)).fetchone()
        if not existing:
            raise HTTPException(404, "Storage connection not found.")
        destination_changed = any(existing[key] != value for key, value in (
            ("bucket", payload.bucket), ("region", payload.region), ("account_id", account), ("endpoint", endpoint)
        ))
        if destination_changed and connection.execute("SELECT 1 FROM stored_file WHERE connection_id=%s LIMIT 1", (connection_id,)).fetchone():
            raise HTTPException(409, "This connection contains registered files. Its storage destination cannot be changed.")
    if bool(payload.access_key) != bool(payload.secret_key):
        raise HTTPException(422, "Replace both access key and secret key together.")
    if not existing and not payload.access_key:
        raise HTTPException(422, "Access key and secret key are required.")
    access = cipher().encrypt(payload.access_key.get_secret_value().encode()).decode() if payload.access_key else existing["access_key_encrypted"]
    secret = cipher().encrypt(payload.secret_key.get_secret_value().encode()).decode() if payload.secret_key else existing["secret_key_encrypted"]
    values = (payload.name, payload.bucket, payload.region, account, endpoint, access, secret, payload.enabled, admin_id)
    try:
        with connection.transaction():
            if existing:
                changed = any(existing[key] != value for key, value in (("bucket", payload.bucket), ("region", payload.region), ("account_id", account), ("endpoint", endpoint))) or payload.access_key is not None
                return connection.execute(f"""UPDATE storage_connection SET name=%s, bucket=%s, region=%s, account_id=%s,
                    endpoint=%s, access_key_encrypted=%s, secret_key_encrypted=%s, enabled=%s, updated_by=%s,
                    updated_at=clock_timestamp(), last_test_status=CASE WHEN %s THEN NULL ELSE last_test_status END
                    WHERE id=%s RETURNING {PUBLIC_COLUMNS}""", (*values, changed, connection_id)).fetchone()
            return connection.execute(f"""INSERT INTO storage_connection (name,bucket,region,account_id,endpoint,
                access_key_encrypted,secret_key_encrypted,enabled,updated_by,provider)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {PUBLIC_COLUMNS}""", (*values, provider)).fetchone()
    except UniqueViolation as error:
        raise HTTPException(409, "This provider and bucket are already configured.") from error


def storage_error_label(error):
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code")
        allowed = {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidToken",
                   "ExpiredToken", "NoSuchBucket", "NoSuchKey", "InvalidArgument", "InvalidRequest",
                   "NotImplemented", "RequestTimeout", "SlowDown", "InternalError", "ServiceUnavailable"}
        return code if code in allowed else "provider request error"
    if isinstance(error, ValueError):
        return "downloaded content mismatch"
    return "SDK or network error"


def check_connection(connection, provider, connection_id):
    saved = connection.execute("SELECT *, last_test_at > clock_timestamp() - INTERVAL '60 seconds' AS recent FROM storage_connection WHERE id=%s AND provider=%s FOR UPDATE", (connection_id, provider)).fetchone()
    if not saved:
        raise HTTPException(404, "Storage connection not found.")
    if saved["recent"]:
        raise HTTPException(429, "Wait 60 seconds before testing again.", headers={"Retry-After": "60"})
    account, endpoint = destination(provider, StorageInput(name=saved["name"], bucket=saved["bucket"], account_id=saved["account_id"], region=saved["region"], endpoint=saved["endpoint"] if provider == "r2" else None))
    try:
        access = cipher().decrypt(saved["access_key_encrypted"].encode()).decode()
        secret = cipher().decrypt(saved["secret_key_encrypted"].encode()).decode()
    except (InvalidToken, UnicodeError) as error:
        raise HTTPException(503, "Unable to unlock storage credentials. Check the integration encryption key.") from error
    key = f".mikan-connection-tests/{secrets.token_hex(24)}"
    content = b"Mikan storage connection test\n"
    result = "failed"
    detail = "Storage test failed. Check the bucket, region, credentials and read/write/delete permissions."
    attempted_upload = False
    uploaded = None
    stage = "Client setup"
    failure = None
    cleanup_failure = None
    try:
        with closing(boto3.client("s3", endpoint_url=endpoint, region_name=saved["region"], aws_access_key_id=access,
                          aws_secret_access_key=secret, config=Config(signature_version="s3v4", connect_timeout=2, read_timeout=3,
                  retries={"total_max_attempts": 1}, proxies={}, s3={"addressing_style": "path"}))) as client:
            try:
                stage = "Bucket access"
                client.head_bucket(Bucket=saved["bucket"])
                stage = "Upload"
                attempted_upload = True
                uploaded = client.put_object(Bucket=saved["bucket"], Key=key, Body=content, ContentType="text/plain")
                stage = "Read"
                version = {"VersionId": uploaded["VersionId"]} if provider == "s3" and uploaded.get("VersionId") else {}
                response = client.get_object(Bucket=saved["bucket"], Key=key, **version)
                try:
                    if response["Body"].read(len(content) + 1) != content:
                        raise ValueError("Storage content mismatch")
                finally:
                    response["Body"].close()
                result, detail = "passed", "Bucket access, write, read and cleanup passed."
            except (BotoCoreError, ClientError, ValueError) as error:
                failure = f"{stage} failed: {storage_error_label(error)}."
                if stage == "Upload" and isinstance(error, ClientError):
                    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                    code = error.response.get("Error", {}).get("Code")
                    if (isinstance(status, int) and 400 <= status < 500 and status != 408) or code in {
                        "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidToken", "ExpiredToken"
                    }:
                        attempted_upload = False
                        failure += " The upload was rejected; cleanup was not attempted."
            finally:
                if attempted_upload:
                    try:
                        version = {"VersionId": uploaded["VersionId"]} if provider == "s3" and uploaded and uploaded.get("VersionId") else {}
                        client.delete_object(Bucket=saved["bucket"], Key=key, **version)
                        if uploaded is None:
                            result, detail = "cleanup_failed", "Upload outcome is uncertain. Check the temporary object and any versions in the bucket."
                    except (BotoCoreError, ClientError) as error:
                        result = "cleanup_failed"
                        cleanup_failure = f"Cleanup failed: {storage_error_label(error)}."
                        cleanup_failure += (" A test object was uploaded; check the reported key and remove it if present."
                                            if uploaded is not None else
                                            " Upload was not confirmed; this does not prove an object exists. Check the reported key before attempting removal.")
    except (BotoCoreError, ClientError, ValueError) as error:
        failure = failure or f"{stage} failed: {storage_error_label(error)}."
    if failure or cleanup_failure:
        detail = " ".join(part for part in (failure, cleanup_failure or (detail if result == "cleanup_failed" else None)) if part)
    connection.execute("UPDATE storage_connection SET last_test_at=clock_timestamp(), last_test_status=%s WHERE id=%s", (result, connection_id))
    if result == "cleanup_failed":
        connection.execute("""UPDATE storage_connection SET last_cleanup=jsonb_build_object(
            'key', %s::text, 'bucket', %s::text, 'endpoint', %s::text, 'reported_at', clock_timestamp()) WHERE id=%s""",
            (key, saved["bucket"], endpoint, connection_id))
    return {"status": result, "detail": detail, "cleanup_key": key if result == "cleanup_failed" else None}


def create_storage_router(admin_dependency, origin_dependency):
    router = APIRouter(prefix="/integrations/storage", dependencies=[Depends(admin_dependency)])
    mutation = [Depends(origin_dependency)]

    @router.get("")
    def summary(connection=Depends(get_db, scope="function")):
        items = connection.execute("SELECT provider, count(*) AS total, count(*) FILTER (WHERE enabled) AS enabled FROM storage_connection GROUP BY provider").fetchall()
        return {"items": items}

    @router.get("/{provider}")
    def listing(provider: Provider, connection=Depends(get_db, scope="function"), search: str = Query("", max_length=160), page: int = Query(1, ge=1, le=100000), from_date: date | None = None, to_date: date | None = None):
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, "Start date must not be after end date.")
        where = "provider=%s AND strpos(lower(name || ' ' || bucket), lower(%s)) > 0 AND (%s::date IS NULL OR created_at >= %s::date::timestamp AT TIME ZONE 'UTC') AND (%s::date IS NULL OR created_at < (%s::date + INTERVAL '1 day') AT TIME ZONE 'UTC')"
        values = (provider, search.strip(), from_date, from_date, to_date, to_date)
        total = connection.execute(f"SELECT count(*) AS total FROM storage_connection WHERE {where}", values).fetchone()["total"]
        items = connection.execute(f"SELECT {PUBLIC_COLUMNS} FROM storage_connection WHERE {where} ORDER BY created_at DESC, id DESC LIMIT 10 OFFSET %s", (*values, (page - 1) * 10)).fetchall()
        return {"items": items, "total": total, "regions": boto3.session.Session().get_available_regions("s3") if provider == "s3" else ["auto"]}

    @router.post("/{provider}", status_code=201, dependencies=mutation)
    def create(provider: Provider, payload: StorageInput, connection=Depends(get_db, scope="function"), admin=Depends(admin_dependency)):
        return save_connection(connection, provider, payload, admin["id"])

    @router.put("/{provider}/{connection_id}", dependencies=mutation)
    def update(provider: Provider, connection_id: int, payload: StorageInput, connection=Depends(get_db, scope="function"), admin=Depends(admin_dependency)):
        return save_connection(connection, provider, payload, admin["id"], connection_id)

    @router.delete("/{provider}/{connection_id}", dependencies=mutation)
    def remove(provider: Provider, connection_id: int, connection=Depends(get_db, scope="function")):
        try:
            with connection.transaction():
                if not connection.execute("DELETE FROM storage_connection WHERE id=%s AND provider=%s RETURNING id", (connection_id, provider)).fetchone():
                    raise HTTPException(404, "Storage connection not found.")
        except ForeignKeyViolation as error:
            raise HTTPException(409, "This connection contains registered files. Disable it instead of removing it.") from error
        return {"detail": "Connection removed. The bucket and its files are unchanged."}

    @router.post("/{provider}/{connection_id}/test", dependencies=mutation)
    def test(provider: Provider, connection_id: int, payload: StorageTestInput, connection=Depends(get_db, scope="function")):
        return check_connection(connection, provider, connection_id)

    return router