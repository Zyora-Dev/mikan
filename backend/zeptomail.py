import os
from pathlib import Path
from typing import Literal

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


ENDPOINTS = {
    "https://cpaas.zoho.in/v1.1/email": "India",
    "https://cpaas.zoho.com/v1.1/email": "United States",
    "https://cpaas.zoho.eu/v1.1/email": "Europe",
    "https://cpaas.zoho.com.au/v1.1/email": "Australia",
    "https://cpaas.zoho.jp/v1.1/email": "Japan",
    "https://cpaas.zoho.com.cn/v1.1/email": "China",
}
PUBLIC_COLUMNS = "endpoint, sender_email, sender_name, enabled, updated_at, last_test_at, last_test_status"
KEY_PATH = Path(__file__).resolve().parent / ".integration-key"


class ZeptoMailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    endpoint: str = Field(max_length=160)
    sender_email: EmailStr = Field(max_length=254)
    sender_name: str = Field(min_length=1, max_length=100)
    token: SecretStr | None = Field(default=None, max_length=4096)
    enabled: bool = False

    @field_validator("endpoint")
    @classmethod
    def approved_endpoint(cls, value):
        if value not in ENDPOINTS:
            raise ValueError("Choose a supported Zoho endpoint")
        return value

    @field_validator("token")
    @classmethod
    def valid_token(cls, value):
        if value is None:
            return value
        raw = value.get_secret_value().strip()
        if raw.startswith("Zoho-enczapikey "):
            raw = raw.removeprefix("Zoho-enczapikey ")
        if not raw or not raw.isascii() or any(character.isspace() or ord(character) < 33 or ord(character) == 127 for character in raw):
            raise ValueError("Invalid API token")
        return SecretStr(raw)


class TestEmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: EmailStr = Field(max_length=254)


def cipher():
    try:
        key = os.environ.get("INTEGRATION_ENCRYPTION_KEY")
        return Fernet(key.encode() if key else KEY_PATH.read_bytes().strip())
    except (OSError, ValueError) as error:
        raise HTTPException(503, "Integration encryption key is not configured.") from error


def public_settings(connection):
    settings = connection.execute(f"SELECT {PUBLIC_COLUMNS} FROM zeptomail_integration WHERE id = 1").fetchone()
    return {"configured": settings is not None, "settings": settings,
            "endpoints": [{"url": url, "label": label} for url, label in ENDPOINTS.items()]}


def save_settings(connection, payload, admin_id):
    encrypted = cipher().encrypt(payload.token.get_secret_value().encode()).decode() if payload.token else None
    saved = connection.execute(
        """INSERT INTO zeptomail_integration (id, endpoint, sender_email, sender_name, token_encrypted, enabled, updated_by)
           SELECT 1, %s, %s, %s, COALESCE(%s, (SELECT token_encrypted FROM zeptomail_integration WHERE id = 1)), %s, %s
           WHERE %s::text IS NOT NULL OR EXISTS (SELECT 1 FROM zeptomail_integration WHERE id = 1)
           ON CONFLICT (id) DO UPDATE SET endpoint = EXCLUDED.endpoint, sender_email = EXCLUDED.sender_email,
           sender_name = EXCLUDED.sender_name, token_encrypted = EXCLUDED.token_encrypted, enabled = EXCLUDED.enabled,
           updated_by = EXCLUDED.updated_by, updated_at = clock_timestamp(), last_test_at = NULL, last_test_status = NULL
           RETURNING id""",
        (payload.endpoint, str(payload.sender_email), payload.sender_name, encrypted, payload.enabled, admin_id, encrypted),
    ).fetchone()
    if not saved:
        raise HTTPException(422, "An API token is required for the first setup.")
    return public_settings(connection)


def send_email(settings, recipient, subject, text):
    if settings["endpoint"] not in ENDPOINTS:
        raise HTTPException(503, "The saved email endpoint is not supported.")
    try:
        token = cipher().decrypt(settings["token_encrypted"].encode()).decode()
    except (InvalidToken, UnicodeError) as error:
        raise HTTPException(503, "Unable to unlock the saved token. Check the integration encryption key.") from error
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.post(settings["endpoint"], headers={"Authorization": f"Zoho-enczapikey {token}"}, json={
                "from": {"address": settings["sender_email"], "name": settings["sender_name"]},
                "to": [{"email_address": {"address": str(recipient)}}],
                "subject": subject, "textbody": text,
                "track_opens": False, "track_clicks": False,
            })
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("message") != "OK":
            raise ValueError("Provider did not confirm acceptance")
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(502, "Zoho did not accept the email. Check the token, data center, verified sender, and available credits.") from error


def test_email(connection, payload):
    settings = connection.execute("SELECT * FROM zeptomail_integration WHERE id = 1 FOR UPDATE").fetchone()
    if not settings:
        raise HTTPException(409, "Save the integration before sending a test email.")
    recent = connection.execute("SELECT last_test_at > CURRENT_TIMESTAMP - INTERVAL '60 seconds' AS recent FROM zeptomail_integration WHERE id = 1").fetchone()
    if recent["recent"]:
        raise HTTPException(429, "Wait 60 seconds before sending another test email.")
    result: Literal["accepted", "failed"] = "accepted"
    detail = "Test email accepted by Zoho. Inbox delivery is not yet confirmed."
    try:
        send_email(settings, payload.recipient, "Mikan email integration test", "This test email was requested from the Mikan super-admin integrations page.")
    except HTTPException as error:
        result, detail = "failed", error.detail
    connection.execute("UPDATE zeptomail_integration SET last_test_at = clock_timestamp(), last_test_status = %s WHERE id = 1", (result,))
    return {"status": result, "detail": detail}