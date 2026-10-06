import hmac
import hashlib
import os
import re
import secrets
import time
from ipaddress import ip_address
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator, model_validator

from database import get_db
from companies import normalize_logo
from security import SESSION_SECONDS, hash_password, hash_token, verify_password
from zeptomail import send_email

TEAM_COOKIE = "mikan_team_session"
SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
ACCOUNT_COLUMNS = "account.id, account.name, account.email, account.mobile, account.role, account.status, account.auth_type, account.team_id, account.company_id, account.created_at, team.name AS team_name"


class TeamInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class TeamFolderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage_quota_bytes: int = Field(ge=0, le=9000000000000000, strict=True)
    manager_can_view_drives: bool = Field(strict=True)
    storage_alerts_enabled: bool | None = Field(default=None, strict=True)
    storage_warning_percent: int | None = Field(default=None, ge=1, le=98, strict=True)
    storage_critical_percent: int | None = Field(default=None, ge=2, le=99, strict=True)

    @model_validator(mode="after")
    def alert_thresholds(self):
        values = (self.storage_alerts_enabled, self.storage_warning_percent, self.storage_critical_percent)
        if any(value is not None for value in values):
            if any(value is None for value in values) or self.storage_warning_percent >= self.storage_critical_percent:
                raise ValueError("Provide alert settings with warning below critical percentage.")
        return self


class InviteInput(TeamInput):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr = Field(max_length=254)
    mobile: str = Field(min_length=7, max_length=25)
    role: Literal["manager", "member"]
    team_id: int = Field(gt=0, le=9223372036854775807, strict=True)

    @field_validator("mobile")
    @classmethod
    def mobile_number(cls, value):
        if not re.fullmatch(r"\+?[0-9 ()-]+", value) or not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Enter a mobile number with 7-15 digits.")
        return value


class ProfileInput(TeamInput):
    name: str = Field(min_length=1, max_length=120)
    mobile: str = Field(min_length=7, max_length=25)
    photo_base64: str | None = Field(default=None, max_length=2796204)
    remove_photo: bool = Field(default=False, strict=True)

    @field_validator("mobile")
    @classmethod
    def mobile_number(cls, value):
        return InviteInput.mobile_number(value)

    @model_validator(mode="after")
    def photo_choice(self):
        if self.remove_photo and self.photo_base64 is not None:
            raise ValueError("Choose either a new photo or remove photo.")
        return self


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr = Field(max_length=254)


class PasswordInput(EmailInput):
    password: SecretStr = Field(min_length=1, max_length=128)


class TokenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=32, max_length=128)


class ActivateInput(TokenInput):
    auth_type: Literal["otp", "password"]
    password: SecretStr | None = Field(default=None, min_length=12, max_length=128)

    @model_validator(mode="after")
    def password_choice(self):
        if (self.auth_type == "password") != (self.password is not None):
            raise ValueError("Password is required only for password sign-in.")
        return self


class OTPInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: SecretStr = Field(min_length=32, max_length=128)
    code: SecretStr = Field(min_length=6, max_length=6)


def limited(connection, key, maximum, seconds):
    digest = hash_token(key)
    row = connection.execute(
        """INSERT INTO team_rate_limit (key, hits) VALUES (%s, 1)
           ON CONFLICT (key) DO UPDATE SET
           hits = CASE WHEN team_rate_limit.window_started_at <= clock_timestamp() - %s * INTERVAL '1 second' THEN 1 ELSE team_rate_limit.hits + 1 END,
           window_started_at = CASE WHEN team_rate_limit.window_started_at <= clock_timestamp() - %s * INTERVAL '1 second' THEN clock_timestamp() ELSE team_rate_limit.window_started_at END
           RETURNING hits""", (digest, seconds, seconds),
    ).fetchone()
    return row["hits"] > maximum


def authentication_client(request):
    secret = os.environ.get('TEAM_PROXY_SECRET', '')
    signature = request.headers.get('x-mikan-client-signature')
    if not secret and not signature:
        return request.client.host if request.client else 'unknown'
    if len(secret) < 32:
        raise HTTPException(503, 'Authentication proxy is not configured.')
    address = request.headers.get('x-mikan-client-ip', '')
    timestamp = request.headers.get('x-mikan-client-time', '')
    try:
        normalized = str(ip_address(address))
        valid_time = re.fullmatch(r'[0-9]{10}', timestamp) and abs(time.time() - int(timestamp)) <= 30
    except ValueError:
        raise HTTPException(403, 'Invalid authentication proxy identity.') from None
    payload = f'{timestamp}\n{request.method}\n{request.url.path}\n{address}'
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not valid_time or not signature or not re.fullmatch(r'[a-f0-9]{64}', signature) or not hmac.compare_digest(expected, signature):
        raise HTTPException(403, 'Invalid authentication proxy identity.')
    return normalized


def slow_down():
    return JSONResponse({"detail": "Too many attempts. Please try again later."}, status_code=429, headers={"Retry-After": "900", "Cache-Control": "no-store"})


def email_settings(connection):
    settings = connection.execute("SELECT * FROM zeptomail_integration WHERE id = 1 AND enabled = TRUE").fetchone()
    if not settings:
        raise HTTPException(503, "Email service is unavailable. Ask the platform administrator to enable Zoho email.")
    return settings


def public_origin():
    origin = os.environ.get("TEAM_PUBLIC_ORIGIN", "http://127.0.0.1:3000").rstrip("/")
    parsed = urlsplit(origin)
    local = parsed.hostname in ("127.0.0.1", "localhost")
    if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path or (parsed.scheme != "https" and not (parsed.scheme == "http" and local and not SECURE)):
        raise HTTPException(503, "Team activation URL is not configured correctly.")
    return origin


def issue_verification(connection, account, purpose):
    settings = email_settings(connection)
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=48 if purpose == "activate" else 1)
    connection.execute("DELETE FROM team_verification WHERE account_id = %s AND purpose = %s", (account["id"], purpose))
    connection.execute("INSERT INTO team_verification (token_hash, account_id, purpose, expires_at) VALUES (%s, %s, %s, %s)", (hash_token(token), account["id"], purpose, expires))
    team = connection.execute("SELECT team.name, company.name AS company_name FROM team JOIN company ON company.id = team.company_id WHERE team.id = %s", (account["team_id"],)).fetchone()
    action = "Activate your Mikan Cloud account" if purpose == "activate" else "Recover your Mikan Cloud account"
    role = "Team Manager" if account["role"] == "manager" else "Member"
    text = f"Hello {account['name']},\n\n{team['company_name']} - {team['name']}\nRole: {role}\n\n{action}:\n{public_origin()}/activate#token={token}\n\nChoose email OTP or password sign-in after verifying this link. This link expires in {'48 hours' if purpose == 'activate' else '1 hour'} and can be used once.\n\nIf you did not expect this email, ignore it."
    send_email(settings, account["email"], action, text)


def verification_account(connection, token):
    return connection.execute(
        """SELECT account.*, verification.purpose, verification.token_hash, team.name AS team_name, company.name AS company_name
           FROM team_verification verification JOIN team_account account ON account.id = verification.account_id
           JOIN team ON team.id = account.team_id JOIN company ON company.id = account.company_id
           WHERE verification.token_hash = %s AND verification.expires_at > clock_timestamp()
           AND ((verification.purpose = 'activate' AND account.status = 'invited') OR
                (verification.purpose = 'recover' AND account.status = 'active')) FOR UPDATE OF account, verification""",
        (hash_token(token),),
    ).fetchone()


def public_account(account):
    return {key: account[key] for key in ("id", "name", "email", "mobile", "role", "auth_type", "team_id", "company_id", "team_name", "company_name", "has_photo")}


def start_session(connection, account, request, response):
    token = secrets.token_urlsafe(32)
    connection.execute("DELETE FROM team_session WHERE token_hash = %s OR expires_at <= clock_timestamp()", (hash_token(request.cookies.get(TEAM_COOKIE, "")),))
    connection.execute("INSERT INTO team_session (token_hash, account_id, expires_at) VALUES (%s, %s, %s)", (hash_token(token), account["id"], datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)))
    response.set_cookie(TEAM_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=SECURE, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


def current_team_account(request: Request, response: Response, connection=Depends(get_db, scope="function")):
    token = request.cookies.get(TEAM_COOKIE, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Sign in required.")
    account = connection.execute(
        f"""SELECT {ACCOUNT_COLUMNS}, company.name AS company_name, account.photo_data IS NOT NULL AS has_photo FROM team_session session
            JOIN team_account account ON account.id = session.account_id JOIN team ON team.id = account.team_id
            JOIN company ON company.id = account.company_id
            WHERE session.token_hash = %s AND session.expires_at > clock_timestamp() AND account.status = 'active'""", (hash_token(token),),
    ).fetchone()
    if not account:
        raise HTTPException(401, "Sign in required.")
    response.headers["Cache-Control"] = "no-store"
    return public_account(account)


def create_team_router(company_dependency, origin_dependency, super_dependency=None):
    router = APIRouter()
    mutation = [Depends(origin_dependency)]

    @router.get("/team/storage")
    def member_storage(response: Response, account=Depends(current_team_account), connection=Depends(get_db, scope="function")):
        usage = connection.execute("""SELECT team.storage_quota_bytes AS quota_bytes,
            team.storage_used_bytes AS team_used_bytes,
            (SELECT coalesce(sum(file.size_bytes),0) FROM stored_file file
             WHERE file.company_id=team.company_id AND file.team_id=team.id AND file.owner_id=%s
             AND file.state NOT IN ('cancelled','purged')) +
            (SELECT coalesce(sum(version.quota_bytes),0) FROM file_version version
             JOIN stored_file file ON file.id=version.file_id AND file.company_id=version.company_id
             WHERE version.company_id=team.company_id AND version.team_id=team.id AND file.owner_id=%s) AS used_bytes
            FROM team WHERE team.id=%s AND team.company_id=%s""",
            (account["id"], account["id"], account["team_id"], account["company_id"])).fetchone()
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Vary"] = "Cookie"
        return {key: int(value) for key, value in usage.items()}

    @router.get("/company/teams/folders")
    def team_folders(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), search: str = Query("", max_length=160), page: int = Query(1, ge=1, le=100000), from_date: date | None = None, to_date: date | None = None):
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, "Start date must not be after end date.")
        where = "company_id=%s AND strpos(lower(name), lower(%s)) > 0 AND (%s::date IS NULL OR created_at >= %s::date::timestamp AT TIME ZONE 'UTC') AND (%s::date IS NULL OR created_at < (%s::date + INTERVAL '1 day') AT TIME ZONE 'UTC')"
        values = (admin["company_id"], search.strip(), from_date, from_date, to_date, to_date)
        total = connection.execute(f"SELECT count(*) AS total FROM team WHERE {where}", values).fetchone()["total"]
        items = connection.execute(f"""SELECT id, name, storage_quota_bytes, storage_used_bytes, manager_can_view_drives, storage_alerts_enabled, storage_warning_percent, storage_critical_percent, created_at,
            (SELECT name FROM team_account WHERE team_id=team.id AND role='manager' AND status <> 'disabled') AS manager,
            (SELECT count(*) FROM team_account WHERE team_id=team.id AND status <> 'disabled') AS people
            FROM team WHERE {where} ORDER BY created_at DESC, id DESC LIMIT 10 OFFSET %s""", (*values, (page - 1) * 10)).fetchall()
        return {"items": items, "total": total}

    @router.post("/company/teams/folders/{team_id}", dependencies=mutation)
    def configure_team_folder(team_id: int, payload: TeamFolderInput, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        with connection.transaction():
            team = connection.execute("SELECT id, name, storage_used_bytes, storage_quota_bytes, manager_can_view_drives, storage_alerts_enabled, storage_warning_percent, storage_critical_percent FROM team WHERE id=%s AND company_id=%s FOR UPDATE", (team_id, admin["company_id"])).fetchone()
            if not team:
                raise HTTPException(404, "Team folder not found.")
            if payload.storage_quota_bytes < team["storage_used_bytes"]:
                raise HTTPException(409, "Allocation cannot be less than used and reserved storage.")
            alert_keys = ("storage_alerts_enabled", "storage_warning_percent", "storage_critical_percent")
            alerts = tuple(getattr(payload, key) if getattr(payload, key) is not None else team[key] for key in alert_keys)
            previous_alerts = tuple(team[key] for key in alert_keys)
            if (team["storage_quota_bytes"], team["manager_can_view_drives"], previous_alerts) != (payload.storage_quota_bytes, payload.manager_can_view_drives, alerts):
                connection.execute("UPDATE team SET storage_quota_bytes=%s, manager_can_view_drives=%s, storage_alerts_enabled=%s, storage_warning_percent=%s, storage_critical_percent=%s WHERE id=%s", (payload.storage_quota_bytes, payload.manager_can_view_drives, *alerts, team_id))
            if previous_alerts != alerts:
                connection.execute("INSERT INTO data_activity(company_id,actor,action,subject,detail) VALUES (%s,%s,'storage_alert_settings',%s,%s)", (admin["company_id"], admin["name"], team["name"], f"Alerts {'on' if alerts[0] else 'off'}; warning {alerts[1]}%; critical {alerts[2]}%; full 100%."))
            if (team["storage_quota_bytes"], team["manager_can_view_drives"]) != (payload.storage_quota_bytes, payload.manager_can_view_drives):
                connection.execute("""INSERT INTO team_folder_activity (company_id, team_id, kind, actor_id, actor_name, previous_quota_bytes, quota_bytes, previous_manager_access, manager_access)
                    VALUES (%s, %s, 'settings_changed', %s, %s, %s, %s, %s, %s)""", (admin["company_id"], team_id, admin["id"], admin["name"], team["storage_quota_bytes"], payload.storage_quota_bytes, team["manager_can_view_drives"], payload.manager_can_view_drives))
        return {"detail": "Team folder settings saved."}

    @router.get("/company/teams/folders/{team_id}/activity")
    def team_folder_activity(team_id: int, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), page: int = Query(1, ge=1, le=100000), from_date: date | None = None, to_date: date | None = None):
        team = connection.execute("SELECT id, name, created_at FROM team WHERE id=%s AND company_id=%s", (team_id, admin["company_id"])).fetchone()
        if not team:
            raise HTTPException(404, "Team folder not found.")
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, "Start date must not be after end date.")
        where = "company_id=%s AND team_id=%s AND (%s::date IS NULL OR created_at >= %s::date::timestamp AT TIME ZONE 'UTC') AND (%s::date IS NULL OR created_at < (%s::date + INTERVAL '1 day') AT TIME ZONE 'UTC')"
        values = (admin["company_id"], team_id, from_date, from_date, to_date, to_date)
        total = connection.execute(f"SELECT count(*) AS total FROM team_folder_activity WHERE {where}", values).fetchone()["total"]
        items = connection.execute(f"""SELECT id, kind, actor_name, previous_quota_bytes, quota_bytes, previous_manager_access, manager_access, created_at
            FROM team_folder_activity WHERE {where} ORDER BY created_at DESC, id DESC LIMIT 10 OFFSET %s""", (*values, (page - 1) * 10)).fetchall()
        return {"team": team, "items": items, "total": total}

    @router.get("/company/teams/")
    def list_teams(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), search: str = Query("", max_length=160), page: int = Query(1, ge=1, le=100000), page_size: int = Query(10, ge=1, le=100), from_date: date | None = None, to_date: date | None = None):
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, "Start date must not be after end date.")
        where = "team.company_id = %s AND strpos(lower(team.name), lower(%s)) > 0 AND (%s::date IS NULL OR team.created_at >= %s::date::timestamp AT TIME ZONE 'UTC') AND (%s::date IS NULL OR team.created_at < (%s::date + INTERVAL '1 day') AT TIME ZONE 'UTC')"
        values = (admin["company_id"], search.strip(), from_date, from_date, to_date, to_date)
        total = connection.execute(f"SELECT count(*) AS total FROM team WHERE {where}", values).fetchone()["total"]
        items = connection.execute(f"""SELECT team.id, team.name, team.created_at,
            (SELECT count(*) FROM team_account WHERE team_id = team.id AND status <> 'disabled') AS people,
            (SELECT name FROM team_account WHERE team_id = team.id AND role = 'manager' AND status <> 'disabled') AS manager,
            (SELECT id FROM team_account WHERE team_id = team.id AND role = 'manager' AND status <> 'disabled') AS manager_id,
            (SELECT status FROM team_account WHERE team_id = team.id AND role = 'manager' AND status <> 'disabled') AS manager_status
            FROM team WHERE {where} ORDER BY team.created_at DESC, team.id DESC LIMIT %s OFFSET %s""", (*values, page_size, (page - 1) * page_size)).fetchall()
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @router.post("/company/teams/", status_code=201, dependencies=mutation)
    def add_team(payload: TeamInput, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        try:
            with connection.transaction():
                team = connection.execute("INSERT INTO team (company_id, name) VALUES (%s, %s) RETURNING id, name, created_at", (admin["company_id"], payload.name)).fetchone()
                connection.execute("""INSERT INTO team_folder_activity (company_id, team_id, kind, actor_id, actor_name, quota_bytes, manager_access, created_at)
                    VALUES (%s, %s, 'created', %s, %s, 0, FALSE, %s)""", (admin["company_id"], team["id"], admin["id"], admin["name"], team["created_at"]))
                return team
        except UniqueViolation as error:
            raise HTTPException(409, "A team with this name already exists.") from error

    @router.get("/company/teams/people")
    def list_people(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), search: str = Query("", max_length=160), page: int = Query(1, ge=1, le=100000), page_size: int = Query(10, ge=1, le=100), team_id: int | None = Query(None, gt=0, le=9223372036854775807), from_date: date | None = None, to_date: date | None = None):
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, "Start date must not be after end date.")
        where = "account.company_id = %s AND (%s::bigint IS NULL OR account.team_id = %s) AND strpos(lower(account.name || ' ' || account.email || ' ' || account.mobile || ' ' || team.name), lower(%s)) > 0 AND (%s::date IS NULL OR account.created_at >= %s::date::timestamp AT TIME ZONE 'UTC') AND (%s::date IS NULL OR account.created_at < (%s::date + INTERVAL '1 day') AT TIME ZONE 'UTC')"
        values = (admin["company_id"], team_id, team_id, search.strip(), from_date, from_date, to_date, to_date)
        source = "FROM team_account account JOIN team ON team.id = account.team_id"
        total = connection.execute(f"SELECT count(*) AS total {source} WHERE {where}", values).fetchone()["total"]
        items = connection.execute(f"SELECT {ACCOUNT_COLUMNS}, (SELECT expires_at FROM team_verification WHERE account_id = account.id AND purpose = 'activate') AS invitation_expires_at {source} WHERE {where} ORDER BY account.created_at DESC, account.id DESC LIMIT %s OFFSET %s", (*values, page_size, (page - 1) * page_size)).fetchall()
        summary = connection.execute("SELECT count(*) FILTER (WHERE status = 'active') AS active, count(*) FILTER (WHERE status = 'invited') AS invited FROM team_account WHERE company_id = %s", (admin["company_id"],)).fetchone()
        return {"items": items, "total": total, "page": page, "page_size": page_size, "summary": summary}

    @router.post("/company/teams/invite", status_code=201, dependencies=mutation)
    def invite(payload: InviteInput, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        if not connection.execute("SELECT id FROM team WHERE id = %s AND company_id = %s FOR UPDATE", (payload.team_id, admin["company_id"])).fetchone():
            raise HTTPException(422, "Select a team in your company.")
        if limited(connection, f"invite:{admin['company_id']}", 100, 3600):
            return slow_down()
        try:
            with connection.transaction():
                account = connection.execute("INSERT INTO team_account (company_id, team_id, name, email, mobile, role) VALUES (%s, %s, %s, %s, %s, %s) RETURNING *", (admin["company_id"], payload.team_id, payload.name, str(payload.email).lower(), payload.mobile, payload.role)).fetchone()
                issue_verification(connection, account, "activate")
        except UniqueViolation as error:
            detail = "This team already has a manager." if error.diag.constraint_name == "team_one_manager" else "This email is already registered or invited."
            raise HTTPException(409, detail) from error
        return {"id": account["id"], "detail": "Member Invited successfully."}

    @router.post("/company/teams/people/{account_id}/edit", dependencies=mutation)
    def edit_person(account_id: int, payload: InviteInput, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        account = connection.execute("SELECT * FROM team_account WHERE id = %s AND company_id = %s FOR UPDATE", (account_id, admin["company_id"])).fetchone()
        if not account:
            raise HTTPException(404, "Person not found.")
        if account["status"] == "disabled":
            raise HTTPException(409, "Revoked accounts cannot be edited.")
        if not connection.execute("SELECT id FROM team WHERE id = %s AND company_id = %s FOR UPDATE", (payload.team_id, admin["company_id"])).fetchone():
            raise HTTPException(422, "Select a team in your company.")
        email = str(payload.email).lower()
        email_changed = email != account["email"].strip().lower()
        if email_changed and limited(connection, f"invite:{admin['company_id']}", 100, 3600):
            return slow_down()
        try:
            with connection.transaction():
                updated = connection.execute(
                    """UPDATE team_account SET name = %s, email = %s, mobile = %s, role = %s, team_id = %s
                       WHERE id = %s RETURNING *""",
                    (payload.name, email, payload.mobile, payload.role, payload.team_id, account_id),
                ).fetchone()
                if email_changed:
                    connection.execute("UPDATE team_account SET status = 'invited', auth_type = NULL, password_hash = NULL, activated_at = NULL WHERE id = %s", (account_id,))
                    for table in ("team_session", "team_otp", "team_verification"):
                        connection.execute(f"DELETE FROM {table} WHERE account_id = %s", (account_id,))
                    issue_verification(connection, updated, "activate")
                elif account["team_id"] != payload.team_id or account["role"] != payload.role:
                    for table in ("team_session", "team_otp"):
                        connection.execute(f"DELETE FROM {table} WHERE account_id = %s", (account_id,))
        except UniqueViolation as error:
            detail = "This team already has a manager." if error.diag.constraint_name == "team_one_manager" else "This email is already registered or invited."
            raise HTTPException(409, detail) from error
        return {"detail": "Member Invited successfully." if email_changed else "Member updated."}

    @router.post("/company/teams/people/{account_id}/resend", dependencies=mutation)
    def resend(account_id: int, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        account = connection.execute("SELECT * FROM team_account WHERE id = %s AND company_id = %s FOR UPDATE", (account_id, admin["company_id"])).fetchone()
        if not account:
            raise HTTPException(404, "Person not found.")
        if account["status"] != "invited":
            raise HTTPException(409, "Only pending invitations can be resent.")
        if connection.execute("SELECT 1 FROM team_verification WHERE account_id = %s AND created_at > clock_timestamp() - INTERVAL '60 seconds'", (account_id,)).fetchone() or limited(connection, f"resend:{account_id}", 5, 3600):
            return slow_down()
        issue_verification(connection, account, "activate")
        return {"detail": "Member Invited successfully."}

    @router.post("/company/teams/people/{account_id}/disable", dependencies=mutation)
    def disable(account_id: int, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        account = connection.execute("UPDATE team_account SET status = 'disabled' WHERE id = %s AND company_id = %s RETURNING id", (account_id, admin["company_id"])).fetchone()
        if not account:
            raise HTTPException(404, "Person not found.")
        for table in ("team_session", "team_otp", "team_verification"):
            connection.execute(f"DELETE FROM {table} WHERE account_id = %s", (account_id,))
        return {"detail": "Access revoked and pending verification links cancelled."}

    if super_dependency:
        def selected_company(company_id: int, admin=Depends(super_dependency), connection=Depends(get_db, scope="function")):
            if not connection.execute('SELECT id FROM company WHERE id=%s', (company_id,)).fetchone():
                raise HTTPException(404, 'Company not found.')
            return {**admin, 'company_id': company_id}

        @router.get('/admin/management/companies/{company_id}/teams')
        def super_teams(connection=Depends(get_db, scope="function"), admin=Depends(selected_company), search: str = Query('', max_length=160), page: int = Query(1, ge=1, le=100000), page_size: int = Query(10, ge=1, le=100), from_date: date | None = None, to_date: date | None = None):
            return list_teams(connection, admin, search, page, page_size, from_date, to_date)

        @router.post('/admin/management/companies/{company_id}/teams', status_code=201, dependencies=mutation)
        def super_add_team(payload: TeamInput, connection=Depends(get_db, scope="function"), admin=Depends(selected_company)):
            return add_team(payload, connection, admin)

        @router.get('/admin/management/companies/{company_id}/teams/people')
        def super_people(connection=Depends(get_db, scope="function"), admin=Depends(selected_company), search: str = Query('', max_length=160), page: int = Query(1, ge=1, le=100000), page_size: int = Query(10, ge=1, le=100), team_id: int | None = Query(None, gt=0, le=9223372036854775807), from_date: date | None = None, to_date: date | None = None):
            return list_people(connection, admin, search, page, page_size, team_id, from_date, to_date)

        @router.post('/admin/management/companies/{company_id}/teams/invite', status_code=201, dependencies=mutation)
        def super_invite(payload: InviteInput, connection=Depends(get_db, scope="function"), admin=Depends(selected_company)):
            return invite(payload, connection, admin)

        @router.post('/admin/management/companies/{company_id}/teams/people/{account_id}/edit', dependencies=mutation)
        def super_edit(account_id: int, payload: InviteInput, connection=Depends(get_db, scope="function"), admin=Depends(selected_company)):
            return edit_person(account_id, payload, connection, admin)

        @router.post('/admin/management/companies/{company_id}/teams/people/{account_id}/resend', dependencies=mutation)
        def super_resend(account_id: int, connection=Depends(get_db, scope="function"), admin=Depends(selected_company)):
            return resend(account_id, connection, admin)

        @router.post('/admin/management/companies/{company_id}/teams/people/{account_id}/disable', dependencies=mutation)
        def super_disable(account_id: int, connection=Depends(get_db, scope="function"), admin=Depends(selected_company)):
            return disable(account_id, connection, admin)

        @router.post('/admin/management/companies/{company_id}/teams/people/{account_id}/delete', dependencies=mutation)
        def super_delete(account_id: int, connection=Depends(get_db, scope="function"), admin=Depends(selected_company)):
            from psycopg.errors import ForeignKeyViolation
            try:
                with connection.transaction():
                    removed = connection.execute('DELETE FROM team_account WHERE id=%s AND company_id=%s RETURNING id', (account_id, admin['company_id'])).fetchone()
                    if not removed:
                        raise HTTPException(404, 'Member not found.')
            except ForeignKeyViolation as error:
                raise HTTPException(409, 'This member has files, folders or workflow records. Revoke access or use Clear Data with the required categories selected.') from error
            return {'detail': 'Member deleted.'}

    @router.post("/auth/team/verification", dependencies=mutation)
    def inspect_verification(payload: TokenInput, connection=Depends(get_db, scope="function")):
        account = verification_account(connection, payload.token.get_secret_value())
        if not account:
            raise HTTPException(400, "This link is expired, already used, or no longer valid.")
        return {key: account[key] for key in ("name", "email", "role", "team_name", "company_name", "purpose")}

    @router.post("/auth/team/activate", dependencies=mutation)
    def activate(payload: ActivateInput, connection=Depends(get_db, scope="function")):
        account = verification_account(connection, payload.token.get_secret_value())
        if not account:
            raise HTTPException(400, "This link is expired, already used, or no longer valid.")
        encoded = hash_password(payload.password.get_secret_value()) if payload.password else None
        connection.execute("UPDATE team_account SET status = 'active', auth_type = %s, password_hash = %s, activated_at = COALESCE(activated_at, clock_timestamp()) WHERE id = %s", (payload.auth_type, encoded, account["id"]))
        for table in ("team_session", "team_otp", "team_verification"):
            connection.execute(f"DELETE FROM {table} WHERE account_id = %s", (account["id"],))
        connection.execute("DELETE FROM team_rate_limit WHERE key = %s", (hash_token(f"login:{account['email']}"),))
        return {"detail": "Account verified. Sign in using your selected method.", "auth_type": payload.auth_type}

    @router.post("/auth/team/password", dependencies=mutation)
    def password_login(payload: PasswordInput, request: Request, response: Response, connection=Depends(get_db, scope="function")):
        email = str(payload.email).lower()
        client = authentication_client(request)
        if limited(connection, f"password-ip:{client}", 60, 900) or limited(connection, f"login:{email}", 5, 900):
            return slow_down()
        account = connection.execute("SELECT * FROM team_account WHERE lower(btrim(email)) = %s FOR UPDATE", (email,)).fetchone()
        encoded = (account["password_hash"] if account else None) or DUMMY_HASH
        valid = verify_password(payload.password.get_secret_value(), encoded)
        if not valid or not account or account["status"] != "active" or account["auth_type"] != "password":
            response.status_code = 401
            return {"detail": "Invalid credentials or sign-in method."}
        connection.execute("DELETE FROM team_rate_limit WHERE key = %s", (hash_token(f"login:{email}"),))
        return start_session(connection, account, request, response)

    @router.post("/auth/team/otp/request", dependencies=mutation)
    def request_otp(payload: EmailInput, request: Request, response: Response, connection=Depends(get_db, scope="function")):
        email = str(payload.email).lower()
        client = authentication_client(request)
        settings = email_settings(connection)
        if limited(connection, f"mail-ip:{client}", 60, 3600) or limited(connection, f"otp-cooldown:{email}", 1, 60) or limited(connection, f"otp-send:{email}", 5, 3600):
            return slow_down()
        challenge = secrets.token_urlsafe(32)
        account = connection.execute("SELECT * FROM team_account WHERE lower(btrim(email)) = %s FOR UPDATE", (email,)).fetchone()
        if account and account["status"] == "active" and account["auth_type"] == "otp":
            code = f"{secrets.randbelow(1000000):06d}"
            try:
                with connection.transaction():
                    connection.execute("DELETE FROM team_otp WHERE account_id = %s", (account["id"],))
                    connection.execute("INSERT INTO team_otp (challenge_hash, account_id, code_hash, expires_at) VALUES (%s, %s, %s, %s)", (hash_token(challenge), account["id"], hash_token(challenge + code), datetime.now(timezone.utc) + timedelta(minutes=10)))
                    send_email(settings, email, "Your Mikan Cloud sign-in code", f"Your Mikan Cloud code is {code}.\n\nIt expires in 10 minutes and can be used once. Never share this code. If you did not request it, ignore this email.")
            except HTTPException:
                response.status_code = 503
                return {"detail": "Email could not be sent. Please try again later."}
        response.status_code = 202
        return {"challenge": challenge, "detail": "If this account is active and uses email OTP, a code has been sent.", "resend_after": 60}

    @router.post("/auth/team/otp/verify", dependencies=mutation)
    def verify_otp(payload: OTPInput, request: Request, response: Response, connection=Depends(get_db, scope="function")):
        if limited(connection, f"verify-ip:{authentication_client(request)}", 60, 900):
            return slow_down()
        challenge = payload.challenge.get_secret_value()
        otp = connection.execute("SELECT otp.*, account.status, account.auth_type, account.email FROM team_otp otp JOIN team_account account ON account.id = otp.account_id WHERE challenge_hash = %s FOR UPDATE OF account, otp", (hash_token(challenge),)).fetchone()
        if otp and limited(connection, f"login:{otp['email']}", 5, 900):
            return slow_down()
        if not otp or otp["status"] != "active" or otp["auth_type"] != "otp" or otp["expires_at"] <= datetime.now(timezone.utc) or otp["failures"] >= 5:
            response.status_code = 401
            return {"detail": "Invalid or expired code."}
        if not hmac.compare_digest(otp["code_hash"], hash_token(challenge + payload.code.get_secret_value())):
            connection.execute("UPDATE team_otp SET failures = failures + 1 WHERE challenge_hash = %s", (hash_token(challenge),))
            response.status_code = 401
            return {"detail": "Invalid or expired code."}
        connection.execute("DELETE FROM team_otp WHERE challenge_hash = %s", (hash_token(challenge),))
        connection.execute("DELETE FROM team_rate_limit WHERE key = %s", (hash_token(f"login:{otp['email']}"),))
        return start_session(connection, {"id": otp["account_id"]}, request, response)

    @router.post("/auth/team/recover", dependencies=mutation)
    def recover(payload: EmailInput, request: Request, response: Response, connection=Depends(get_db, scope="function")):
        client = authentication_client(request)
        email_settings(connection)
        email = str(payload.email).lower()
        if limited(connection, f"recover-ip:{client}", 30, 3600) or limited(connection, f"recover:{email}", 3, 3600):
            return slow_down()
        account = connection.execute("SELECT * FROM team_account WHERE lower(btrim(email)) = %s FOR UPDATE", (email,)).fetchone()
        if account and account["status"] == "active":
            try:
                with connection.transaction():
                    issue_verification(connection, account, "recover")
            except HTTPException:
                response.status_code = 503
                return {"detail": "Email could not be sent. Please try again later."}
        response.status_code = 202
        return {"detail": "If the account is active, an account recovery link has been sent."}

    router.add_api_route("/auth/team/me", current_team_account, methods=["GET"])

    @router.post("/team/profile", dependencies=mutation)
    def update_profile(payload: ProfileInput, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        photo = normalize_logo(payload.photo_base64, label="photo")
        replace_photo = payload.remove_photo or payload.photo_base64 is not None
        updated = connection.execute(
            """UPDATE team_account SET name=%s, mobile=%s,
               photo_data=CASE WHEN %s THEN %s ELSE photo_data END
               WHERE id=%s AND company_id=%s AND status='active'
               RETURNING name, mobile, photo_data IS NOT NULL AS has_photo""",
            (payload.name, payload.mobile, replace_photo, photo, account["id"], account["company_id"]),
        ).fetchone()
        if not updated:
            raise HTTPException(401, "Sign in required.")
        return {**account, **updated}

    @router.get("/team/profile/photo")
    def profile_photo(connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        row = connection.execute("SELECT photo_data FROM team_account WHERE id=%s AND company_id=%s AND status='active'", (account["id"], account["company_id"])).fetchone()
        if not row or row["photo_data"] is None:
            raise HTTPException(404, "Profile photo not found.")
        return Response(bytes(row["photo_data"]), media_type="image/webp", headers={
            "Cache-Control": "private, no-store, max-age=0", "Vary": "Cookie",
            "X-Content-Type-Options": "nosniff", "Content-Disposition": 'inline; filename="profile.webp"',
        })

    @router.post("/auth/team/logout", dependencies=mutation)
    def team_logout(request: Request, response: Response, connection=Depends(get_db, scope="function")):
        connection.execute("DELETE FROM team_session WHERE token_hash = %s", (hash_token(request.cookies.get(TEAM_COOKIE, "")),))
        response.delete_cookie(TEAM_COOKIE, httponly=True, secure=SECURE, samesite="strict", path="/")
        return {"ok": True}

    @router.get("/team/dashboard")
    def manager_dashboard(response: Response, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        if account['role'] != 'manager':
            raise HTTPException(403, 'Only team managers can view the team dashboard.')
        scope = (account['company_id'], account['team_id'])
        team = connection.execute('''SELECT name,storage_quota_bytes AS quota_bytes,storage_used_bytes AS used_bytes,
            manager_can_view_drives FROM team WHERE company_id=%s AND id=%s''', scope).fetchone()
        team['remaining_bytes'] = max(0, team['quota_bytes'] - team['used_bytes'])
        team['members'] = connection.execute("SELECT count(*) AS total FROM team_account WHERE company_id=%s AND team_id=%s AND status='active'", scope).fetchone()['total']
        team['files'] = connection.execute("SELECT count(*) AS total FROM stored_file WHERE company_id=%s AND team_id=%s AND state='ready'", scope).fetchone()['total']
        team['pending_reviews'] = connection.execute('''SELECT count(*) AS total FROM workflow_run run
            WHERE run.company_id=%s AND run.status='pending' AND EXISTS (SELECT 1 FROM workflow_task task
            WHERE task.run_id=run.id AND task.reviewer_team_id=%s AND task.reviewer_id=%s AND task.status='pending')''',
            (*scope, account['id'])).fetchone()['total']
        team['recent_files'] = []
        team['largest_files'] = []
        if team['manager_can_view_drives']:
            for key, ordering in (('recent_files', 'file.created_at DESC,file.id DESC'), ('largest_files', 'file.size_bytes DESC,lower(file.name),file.id')):
                team[key] = connection.execute(f'''SELECT file.id,file.name,file.folder,file.owner_id,file.size_bytes,file.created_at,owner.name AS owner_name
                    FROM stored_file file JOIN team_account owner ON owner.id=file.owner_id
                    AND owner.company_id=file.company_id AND owner.team_id=file.team_id AND owner.status='active'
                    WHERE file.company_id=%s AND file.team_id=%s AND file.state='ready'
                    ORDER BY {ordering} LIMIT 5''', scope).fetchall()
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        response.headers['Vary'] = 'Cookie'
        return team

    @router.get("/team/people")
    def own_team_people(connection=Depends(get_db, scope="function"), account=Depends(current_team_account), page: int = Query(1, ge=1, le=100000), page_size: int = Query(10, ge=1, le=100), search: str = Query("", max_length=160), from_date: date | None = None, to_date: date | None = None):
        if account["role"] != "manager":
            raise HTTPException(403, "Only team managers can view team members.")
        if from_date and to_date and from_date > to_date:
            raise HTTPException(422, "Start date must not be after end date.")
        where = "company_id = %s AND team_id = %s AND status = 'active' AND strpos(lower(name), lower(%s)) > 0 AND (%s::date IS NULL OR activated_at >= %s::date::timestamp AT TIME ZONE 'UTC') AND (%s::date IS NULL OR activated_at < (%s::date + INTERVAL '1 day') AT TIME ZONE 'UTC')"
        values = (account["company_id"], account["team_id"], search.strip(), from_date, from_date, to_date, to_date)
        total = connection.execute(f"SELECT count(*) AS total FROM team_account WHERE {where}", values).fetchone()["total"]
        items = connection.execute(f"SELECT id, name, role, activated_at FROM team_account WHERE {where} ORDER BY role, name, id LIMIT %s OFFSET %s", (*values, page_size, (page - 1) * page_size)).fetchall()
        identifiers = [person['id'] for person in items]
        usage = connection.execute('''SELECT owner_id,sum(used_bytes) AS used_bytes,sum(file_count) AS file_count FROM (
            SELECT owner_id,sum(size_bytes) AS used_bytes,count(*) FILTER (WHERE state='ready') AS file_count
            FROM stored_file WHERE company_id=%s AND team_id=%s AND owner_id=ANY(%s) AND state NOT IN ('cancelled','purged') GROUP BY owner_id
            UNION ALL SELECT file.owner_id,sum(version.quota_bytes),0 FROM file_version version
            JOIN stored_file file ON file.id=version.file_id AND file.company_id=version.company_id
            WHERE version.company_id=%s AND version.team_id=%s AND file.owner_id=ANY(%s) GROUP BY file.owner_id
            ) charged GROUP BY owner_id''', (account['company_id'], account['team_id'], identifiers, account['company_id'], account['team_id'], identifiers)).fetchall()
        by_owner = {row['owner_id']: row for row in usage}
        for person in items:
            charged = by_owner.get(person['id'], {})
            person.update(used_bytes=int(charged.get('used_bytes', 0)), file_count=int(charged.get('file_count', 0)))
        permission = connection.execute('SELECT manager_can_view_drives FROM team WHERE company_id=%s AND id=%s', (account['company_id'], account['team_id'])).fetchone()
        return {"items": items, "total": total, "page": page, **permission}

    return router