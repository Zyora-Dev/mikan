from datetime import date

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from database import get_db
from workflows import date_clause, list_rows
from zeptomail import cipher


GRAPH_URL = "https://graph.facebook.com/v25.0"
CONSENT_COLUMNS = "id, company_name, recipient, source, consented_at, revoked_at, last_attempt_at, last_status, last_message_id"


class SettingsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    phone_number_id: str = Field(pattern=r"^[1-9][0-9]{4,29}$")
    business_id: str = Field(pattern=r"^[1-9][0-9]{4,29}$")
    access_token: SecretStr | None = Field(default=None, max_length=4096)

    @field_validator("access_token")
    @classmethod
    def valid_token(cls, value):
        if value is not None:
            raw = value.get_secret_value().strip()
            if not raw or not raw.isascii() or any(ord(character) < 33 or ord(character) == 127 for character in raw):
                raise ValueError("Invalid access token")
            return SecretStr(raw)
        return value


class ConsentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    company_name: str = Field(min_length=1, max_length=160)
    recipient: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    source: str = Field(min_length=3, max_length=500)
    opted_in: bool = Field(strict=True)

    @field_validator("opted_in")
    @classmethod
    def explicit_consent(cls, value):
        if not value:
            raise ValueError("Explicit opt-in is required")
        return value


class SendInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    consent_id: int = Field(gt=0, le=9223372036854775807, strict=True)
    company_name: str = Field(min_length=1, max_length=160, pattern=r"^[^\r\n\t]+$")


def public_settings(connection):
    settings = connection.execute("SELECT phone_number_id, business_id, updated_at FROM whatsapp_integration WHERE id=1").fetchone()
    return {"configured": settings is not None, "settings": settings}


def send_template(settings, recipient, company_name):
    try:
        token = cipher().decrypt(settings["token_encrypted"].encode()).decode()
    except (InvalidToken, UnicodeError) as error:
        raise HTTPException(503, "Unable to unlock the saved access token.") from error
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = client.post(f"{GRAPH_URL}/{settings['phone_number_id']}/messages", headers={"Authorization": f"Bearer {token}"}, json={
                "messaging_product": "whatsapp", "recipient_type": "individual", "to": recipient,
                "type": "template", "template": {"name": "onboarding_client", "language": {"code": "en"},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": company_name}]}]},
            })
        if 400 <= response.status_code < 500:
            return {"status": "failed", "message_id": None, "detail": "Meta rejected the message. Check credentials, recipient eligibility and the approved onboarding_client (en) template."}
        response.raise_for_status()
        result = response.json()
        messages = result.get("messages") if isinstance(result, dict) else None
        message_id = messages[0].get("id") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
        if not isinstance(message_id, str) or not message_id or len(message_id) > 512:
            raise ValueError("Missing acceptance ID")
        return {"status": "accepted", "message_id": message_id, "detail": "Accepted by WhatsApp. Delivery is not yet confirmed."}
    except (httpx.HTTPError, ValueError):
        return {"status": "unknown", "message_id": None, "detail": "Send outcome is unknown. Check WhatsApp before sending again to avoid a duplicate."}


def create_whatsapp_router(admin_dependency, origin_dependency):
    router = APIRouter(prefix="/integrations/whatsapp", dependencies=[Depends(admin_dependency)])

    @router.get("")
    def settings(connection=Depends(get_db, scope="function")):
        return public_settings(connection)

    @router.put("", dependencies=[Depends(origin_dependency)])
    def save(payload: SettingsInput, admin=Depends(admin_dependency), connection=Depends(get_db, scope="function")):
        encrypted = cipher().encrypt(payload.access_token.get_secret_value().encode()).decode() if payload.access_token else None
        saved = connection.execute(
            """INSERT INTO whatsapp_integration (id, phone_number_id, business_id, token_encrypted, updated_by)
               SELECT 1,%s,%s,COALESCE(%s,(SELECT token_encrypted FROM whatsapp_integration WHERE id=1)),%s
               WHERE %s::text IS NOT NULL OR EXISTS (SELECT 1 FROM whatsapp_integration WHERE id=1)
               ON CONFLICT (id) DO UPDATE SET phone_number_id=EXCLUDED.phone_number_id, business_id=EXCLUDED.business_id,
               token_encrypted=EXCLUDED.token_encrypted, updated_by=EXCLUDED.updated_by, updated_at=clock_timestamp() RETURNING id""",
            (payload.phone_number_id, payload.business_id, encrypted, admin["id"], encrypted),
        ).fetchone()
        if not saved:
            raise HTTPException(422, "An access token is required for the first setup.")
        return public_settings(connection)

    @router.get("/consents")
    def consents(search: str = Query("", max_length=160), page: int = Query(1, ge=1, le=100000),
                 active: bool = False, from_date: date | None = None, to_date: date | None = None,
                 connection=Depends(get_db, scope="function")):
        clause, values = date_clause(from_date, to_date, "consented_at")
        return list_rows(connection, "whatsapp_consent", CONSENT_COLUMNS,
                         f"{clause} AND (%s=FALSE OR revoked_at IS NULL) AND (company_name ILIKE %s OR recipient ILIKE %s)",
                         [*values, active, f"%{search}%", f"%{search}%"], page, "consented_at DESC, id DESC")

    @router.post("/consents", dependencies=[Depends(origin_dependency)])
    def consent(payload: ConsentInput, admin=Depends(admin_dependency), connection=Depends(get_db, scope="function")):
        saved = connection.execute(
            f"""INSERT INTO whatsapp_consent (company_name, recipient, source, recorded_by)
                VALUES (%s,%s,%s,%s) ON CONFLICT (recipient) WHERE revoked_at IS NULL DO NOTHING RETURNING {CONSENT_COLUMNS}""",
            (payload.company_name, payload.recipient, payload.source, admin["id"]),
        ).fetchone()
        if not saved:
            raise HTTPException(409, "This number already has active consent. Revoke it before recording a replacement.")
        return saved

    @router.post("/consents/{identifier}/revoke", dependencies=[Depends(origin_dependency)])
    def revoke(identifier: int = Path(gt=0, le=9223372036854775807), connection=Depends(get_db, scope="function")):
        saved = connection.execute("UPDATE whatsapp_consent SET revoked_at=COALESCE(revoked_at,clock_timestamp()) WHERE id=%s RETURNING id", (identifier,)).fetchone()
        if not saved:
            raise HTTPException(404, "Consent not found.")
        return {"revoked": True}

    @router.post("/send", dependencies=[Depends(origin_dependency)])
    def send(payload: SendInput, connection=Depends(get_db, scope="function")):
        consent = connection.execute("SELECT *, last_attempt_at > clock_timestamp()-INTERVAL '60 seconds' AS recent FROM whatsapp_consent WHERE id=%s FOR UPDATE", (payload.consent_id,)).fetchone()
        if not consent or consent["revoked_at"]:
            raise HTTPException(409, "An active consent record is required.")
        if consent["recent"]:
            raise HTTPException(429, "Wait 60 seconds before sending to this recipient again.")
        settings = connection.execute("SELECT * FROM whatsapp_integration WHERE id=1 FOR SHARE").fetchone()
        if not settings:
            raise HTTPException(409, "Save WhatsApp credentials before sending.")
        result = send_template(settings, consent["recipient"], payload.company_name)
        connection.execute("UPDATE whatsapp_consent SET last_attempt_at=clock_timestamp(), last_status=%s, last_message_id=%s WHERE id=%s", (result["status"], result["message_id"], consent["id"]))
        return result

    return router