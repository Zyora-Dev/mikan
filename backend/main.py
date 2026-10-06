import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, SecretStr

from database import get_db
from companies import COMPANY_COLUMNS, CompanyInput, normalize_logo
from company_admins import ADMIN_COLUMNS, CompanyAdminInput, CompanyAdminUpdate, create_company_admin, update_company_admin, delete_company_admin
from security import SESSION_SECONDS, hash_password, hash_token, verify_password
from zeptomail import TestEmailInput, ZeptoMailInput, public_settings, save_settings, test_email
from teams import create_team_router, current_team_account
from storage import create_storage_router
from files import PRIVATE_HEADERS, PREVIEW_CSP, PREVIEW_TYPES, create_file_share_router, deliver_share_emails, router as files_router
from workflows import create_workflow_router
from uploads import create_upload_router
from file_versions import create_version_router
from data_administration import create_data_router
from insights import create_insights_router
from notifications import create_notification_router
from automation import create_automation_router, tick
from trash import create_trash_router, cleanup_trash
from management import create_management_router
from whatsapp import create_whatsapp_router


@asynccontextmanager
async def lifespan(application):
    stopping = asyncio.Event()

    async def worker():
        while not stopping.is_set():
            for operation in (deliver_share_emails, tick, cleanup_trash):
                try:
                    await asyncio.to_thread(operation)
                except Exception as error:
                    logging.getLogger(__name__).warning('Worker %s failed (%s)', operation.__name__, type(error).__name__)
            try:
                await asyncio.wait_for(stopping.wait(), timeout=15)
            except TimeoutError:
                pass

    task = asyncio.create_task(worker()) if os.environ.get('MIKAN_AUTOMATION_ENABLED', 'true').lower() == 'true' else None
    try:
        yield
    finally:
        stopping.set()
        if task:
            await task


app = FastAPI(title="Mikan API", lifespan=lifespan)
COOKIE_NAME = "mikan_admin_session"
COMPANY_COOKIE_NAME = "mikan_company_admin_session"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
ALLOWED_ORIGINS = set(os.environ.get(
    "ADMIN_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000"
).split(","))
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
Database = Annotated[psycopg.Connection, Depends(get_db, scope="function")]


@app.middleware("http")
async def private_file_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/integrations/whatsapp'):
        response.headers.update(PRIVATE_HEADERS)
    if request.url.path == "/team/files" or request.url.path.startswith(("/team/files/", "/company/teams/data/", "/company/teams/insights/", "/admin/management/", "/company-admins/", "/companies/", "/admin/insights/", "/admin/notifications", "/company/notifications", "/team/notifications")):
        response.headers.update(PRIVATE_HEADERS)
        if request.url.path.startswith("/team/files/") and request.url.path.endswith("/preview") and response.status_code in (200, 206) and response.headers.get("content-type") in PREVIEW_TYPES.values():
            response.headers["Content-Security-Policy"] = PREVIEW_CSP
    return response


class LoginInput(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)


@app.exception_handler(psycopg.Error)
def database_error(request: Request, error: psycopg.Error):
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable."})


@app.exception_handler(RequestValidationError)
def validation_error(request: Request, error: RequestValidationError):
    if request.url.path.startswith('/admin/management/'):
        return JSONResponse(status_code=422, content={'detail': 'Check the selected company, categories, member details and confirmation.'})
    if request.url.path.startswith(("/admin/notifications", "/company/notifications", "/team/notifications")):
        return JSONResponse(status_code=422, content={"detail": "Check notification filters and submitted details."})
    if request.url.path.startswith(("/company/teams/insights/", "/admin/insights/")):
        fields = sorted({str(issue["loc"][-1]) for issue in error.errors() if issue["loc"]})
        return JSONResponse(status_code=422, content={"detail": "Check report filters: " + ", ".join(fields) + "."})
    if request.url.path.startswith(("/auth/team", "/company/teams", "/team/")):
        fields = sorted({str(issue["loc"][-1]) for issue in error.errors() if issue["loc"]})
        return JSONResponse(status_code=422, content={"detail": "Check the submitted details: " + ", ".join(fields) + ". Passwords must be 12-128 characters."})
    if request.url.path.startswith("/company-admins"):
        fields = sorted({str(issue["loc"][-1]) for issue in error.errors() if issue["loc"]})
        return JSONResponse(status_code=422, content={"detail": "Check admin details: " + ", ".join(fields) + ". Password must be 12-128 characters."})
    if request.url.path.startswith("/integrations"):
        fields = sorted({str(issue["loc"][-1]) for issue in error.errors() if issue["loc"]})
        return JSONResponse(status_code=422, content={"detail": "Check integration details: " + ", ".join(fields) + "."})
    if request.url.path.startswith("/companies"):
        fields = sorted({str(issue["loc"][-1]) for issue in error.errors() if issue["loc"]})
        return JSONResponse(status_code=422, content={"detail": "Check company details: " + ", ".join(fields) + "."})
    return JSONResponse(status_code=422, content={"detail": "Enter a valid email and password (maximum 128 characters)."})


def check_origin(request: Request):
    if request.headers.get("origin") not in ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Request origin not allowed.")


def public_admin(admin):
    return {key: admin[key] for key in ("id", "email", "name", "role")}


@app.post("/auth/admin/login", dependencies=[Depends(check_origin)])
def login(payload: LoginInput, request: Request, response: Response, connection: Database):
    return authenticate(payload, request, response, connection, "super_admin", COOKIE_NAME)


def authenticate(payload, request, response, connection, role, cookie_name):
    email = str(payload.email).strip().lower()
    now = datetime.now(timezone.utc)
    authenticated = False
    throttled = False
    token = None
    with connection.transaction():
        connection.execute(
            "INSERT INTO admin_login_attempt (email) VALUES (%s) ON CONFLICT DO NOTHING",
            (email,),
        )
        attempt = connection.execute(
            "SELECT * FROM admin_login_attempt WHERE email = %s FOR UPDATE", (email,)
        ).fetchone()
        failures = attempt["failures"]
        if attempt["window_started_at"] <= now - timedelta(minutes=15):
            failures = 0
            connection.execute(
                "UPDATE admin_login_attempt SET failures = 0, window_started_at = %s WHERE email = %s",
                (now, email),
            )
        throttled = failures >= 5
        if not throttled:
            admin = connection.execute(
                "SELECT * FROM admin WHERE lower(btrim(email)) = %s", (email,)
            ).fetchone()
            encoded = (admin["password_hash"] if admin else None) or DUMMY_HASH
            valid = verify_password(payload.password.get_secret_value(), encoded)
            authenticated = bool(valid and admin and admin["password_hash"] and admin["is_active"] and admin["role"] == role)
            if authenticated:
                token = secrets.token_urlsafe(32)
                previous = request.cookies.get(cookie_name, "")
                connection.execute("DELETE FROM admin_session WHERE token_hash = %s OR expires_at <= %s", (hash_token(previous), now))
                connection.execute(
                    "INSERT INTO admin_session (token_hash, admin_id, expires_at) VALUES (%s, %s, %s)",
                    (hash_token(token), admin["id"], now + timedelta(seconds=SESSION_SECONDS)),
                )
                connection.execute("DELETE FROM admin_login_attempt WHERE email = %s", (email,))
            else:
                connection.execute("UPDATE admin_login_attempt SET failures = failures + 1 WHERE email = %s", (email,))
    if throttled:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in 15 minutes.", headers={"Retry-After": "900"})
    if not authenticated:
        response.status_code = 401
        return {"detail": "Invalid email or password."}
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(cookie_name, token, max_age=SESSION_SECONDS, httponly=True, secure=COOKIE_SECURE, samesite="strict", path="/")
    return public_admin(admin)


@app.get("/auth/admin/me")
def current_admin(request: Request, response: Response, connection: Database):
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Sign in required.")
    admin = connection.execute(
        """SELECT admin.* FROM admin_session JOIN admin ON admin.id = admin_session.admin_id
           WHERE token_hash = %s AND expires_at > CURRENT_TIMESTAMP
           AND admin.is_active = TRUE AND admin.role = 'super_admin'""",
        (hash_token(token),),
    ).fetchone()
    if not admin:
        raise HTTPException(status_code=401, detail="Sign in required.")
    response.headers["Cache-Control"] = "no-store"
    return public_admin(admin)


@app.post("/auth/admin/logout", dependencies=[Depends(check_origin)])
def logout(request: Request, response: Response, connection: Database):
    connection.execute("DELETE FROM admin_session WHERE token_hash = %s", (hash_token(request.cookies.get(COOKIE_NAME, "")),))
    response.delete_cookie(COOKIE_NAME, httponly=True, secure=COOKIE_SECURE, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


SuperAdmin = Annotated[dict, Depends(current_admin)]


@app.post("/auth/company/admin/login", dependencies=[Depends(check_origin)])
def company_login(payload: LoginInput, request: Request, response: Response, connection: Database):
    return authenticate(payload, request, response, connection, "admin", COMPANY_COOKIE_NAME)


@app.get("/auth/company/admin/me")
def current_company_admin(request: Request, response: Response, connection: Database):
    token = request.cookies.get(COMPANY_COOKIE_NAME)
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Sign in required.")
    admin = connection.execute(
        """SELECT admin.id, admin.name, admin.email, admin.role, admin.mobile,
                  admin.company_id, company.name AS company_name
           FROM admin_session JOIN admin ON admin.id = admin_session.admin_id
           JOIN company ON company.id = admin.company_id
           WHERE token_hash = %s AND expires_at > CURRENT_TIMESTAMP
           AND admin.is_active = TRUE AND admin.role = 'admin'""",
        (hash_token(token),),
    ).fetchone()
    if not admin:
        raise HTTPException(status_code=401, detail="Sign in required.")
    response.headers["Cache-Control"] = "no-store"
    return admin


CompanyAdmin = Annotated[dict, Depends(current_company_admin)]

app.include_router(create_team_router(current_company_admin, check_origin, current_admin))
app.include_router(create_management_router(current_admin, check_origin))
app.include_router(create_whatsapp_router(current_admin, check_origin))
app.include_router(create_storage_router(current_admin, check_origin))
app.include_router(files_router)
app.include_router(create_file_share_router(check_origin))
app.include_router(create_version_router(check_origin))
app.include_router(create_upload_router(current_company_admin, check_origin))
app.include_router(create_data_router(current_company_admin, check_origin))
app.include_router(create_trash_router(current_company_admin, check_origin))
app.include_router(create_insights_router(current_company_admin, True))
app.include_router(create_insights_router(current_admin, False))
app.include_router(create_notification_router(current_admin, check_origin, "super"))
app.include_router(create_notification_router(current_company_admin, check_origin, "company"))
app.include_router(create_notification_router(current_team_account, check_origin, "team"))
app.include_router(create_automation_router(current_company_admin, check_origin))
app.include_router(create_workflow_router(current_company_admin, check_origin))


@app.get("/company/profile")
def own_company(connection: Database, admin: CompanyAdmin):
    return read_company(admin["company_id"], connection)


@app.put("/company/profile", dependencies=[Depends(check_origin)])
def edit_own_company(payload: CompanyInput, connection: Database, admin: CompanyAdmin):
    return save_company(admin["company_id"], payload, connection)


@app.get("/company/profile/logo")
def own_company_logo(connection: Database, admin: CompanyAdmin):
    return read_company_logo(admin["company_id"], connection)


@app.post("/auth/company/admin/logout", dependencies=[Depends(check_origin)])
def company_logout(request: Request, response: Response, connection: Database):
    connection.execute("DELETE FROM admin_session WHERE token_hash = %s", (hash_token(request.cookies.get(COMPANY_COOKIE_NAME, "")),))
    response.delete_cookie(COMPANY_COOKIE_NAME, httponly=True, secure=COOKIE_SECURE, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@app.post("/company-admins/", status_code=201, dependencies=[Depends(check_origin)])
def add_company_admin(payload: CompanyAdminInput, connection: Database, admin: SuperAdmin):
    return create_company_admin(connection, payload)


@app.put("/company-admins/{identifier}", dependencies=[Depends(check_origin)])
def edit_company_admin(identifier: int, payload: CompanyAdminUpdate, connection: Database, admin: SuperAdmin):
    return update_company_admin(connection, identifier, payload)


@app.delete("/company-admins/{identifier}", dependencies=[Depends(check_origin)])
def remove_company_admin(identifier: int, connection: Database, admin: SuperAdmin):
    return delete_company_admin(connection, identifier)


@app.get("/company-admins/")
def list_company_admins(connection: Database, admin: SuperAdmin, search: str = Query(default="", max_length=160),
                        page: int = Query(default=1, ge=1, le=100000), page_size: int = Query(default=10, ge=1, le=100),
                        from_date: date | None = None, to_date: date | None = None):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=422, detail="Start date must not be after end date.")
    filters = ["admin.role = 'admin'", "strpos(lower(admin.name || ' ' || admin.email || ' ' || admin.mobile || ' ' || company.name), lower(%s)) > 0"]
    values = [search.strip()]
    if from_date:
        filters.append("admin.created_at >= (%s::date::timestamp AT TIME ZONE 'UTC')")
        values.append(from_date)
    if to_date:
        filters.append("admin.created_at < ((%s::date + INTERVAL '1 day') AT TIME ZONE 'UTC')")
        values.append(to_date)
    where = " AND ".join(filters)
    source = "FROM admin JOIN company ON company.id = admin.company_id"
    total = connection.execute(f"SELECT count(*) AS total {source} WHERE {where}", values).fetchone()["total"]
    items = connection.execute(
        f"SELECT {ADMIN_COLUMNS} {source} WHERE {where} ORDER BY admin.created_at DESC, admin.id DESC LIMIT %s OFFSET %s",
        [*values, page_size, (page - 1) * page_size],
    ).fetchall()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/integrations/zeptomail")
def get_zeptomail(connection: Database, admin: SuperAdmin):
    return public_settings(connection)


@app.put("/integrations/zeptomail", dependencies=[Depends(check_origin)])
def put_zeptomail(payload: ZeptoMailInput, connection: Database, admin: SuperAdmin):
    return save_settings(connection, payload, admin["id"])


@app.post("/integrations/zeptomail/test", dependencies=[Depends(check_origin)])
def test_zeptomail(payload: TestEmailInput, connection: Database, admin: SuperAdmin, response: Response):
    result = test_email(connection, payload)
    if result["status"] == "failed":
        response.status_code = 502
    return result


@app.delete("/integrations/zeptomail", dependencies=[Depends(check_origin)])
def delete_zeptomail(connection: Database, admin: SuperAdmin):
    connection.execute("DELETE FROM zeptomail_integration WHERE id = 1")
    return public_settings(connection)


@app.get("/companies/")
def list_companies(connection: Database, admin: SuperAdmin, search: str = Query(default="", max_length=160),
                   page: int = Query(default=1, ge=1, le=100000), page_size: int = Query(default=10, ge=1, le=100),
                   from_date: date | None = None, to_date: date | None = None):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=422, detail="Start date must not be after end date.")
    filters = ["strpos(lower(name || ' ' || email || ' ' || mobile), lower(%s)) > 0"]
    values = [search.strip()]
    if from_date:
        filters.append("created_at >= (%s::date::timestamp AT TIME ZONE 'UTC')")
        values.append(from_date)
    if to_date:
        filters.append("created_at < ((%s::date + INTERVAL '1 day') AT TIME ZONE 'UTC')")
        values.append(to_date)
    where = " AND ".join(filters)
    total = connection.execute(f"SELECT count(*) AS total FROM company WHERE {where}", values).fetchone()["total"]
    items = connection.execute(
        f"SELECT {COMPANY_COLUMNS} FROM company WHERE {where} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
        [*values, page_size, (page - 1) * page_size],
    ).fetchall()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/companies/", status_code=201, dependencies=[Depends(check_origin)])
def create_company(payload: CompanyInput, connection: Database, admin: SuperAdmin):
    logo = normalize_logo(payload.logo_base64)
    return connection.execute(
        f"""INSERT INTO company (name, mobile, email, website, address, logo_data, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {COMPANY_COLUMNS}""",
        (payload.name, payload.mobile, str(payload.email), payload.website, payload.address, logo, admin["id"]),
    ).fetchone()


@app.get("/companies/{company_id}")
def get_company(company_id: int, connection: Database, admin: SuperAdmin):
    return read_company(company_id, connection)


def read_company(company_id: int, connection):
    company = connection.execute(f"SELECT {COMPANY_COLUMNS} FROM company WHERE id = %s", (company_id,)).fetchone()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")
    return company


@app.put("/companies/{company_id}", dependencies=[Depends(check_origin)])
def update_company(company_id: int, payload: CompanyInput, connection: Database, admin: SuperAdmin):
    return save_company(company_id, payload, connection)


def save_company(company_id: int, payload: CompanyInput, connection):
    logo = normalize_logo(payload.logo_base64)
    replace_logo = payload.remove_logo or payload.logo_base64 is not None
    company = connection.execute(
        f"""UPDATE company SET name = %s, mobile = %s, email = %s, website = %s, address = %s,
            logo_data = CASE WHEN %s THEN %s ELSE logo_data END, updated_at = clock_timestamp()
            WHERE id = %s RETURNING {COMPANY_COLUMNS}""",
        (payload.name, payload.mobile, str(payload.email), payload.website, payload.address, replace_logo, logo, company_id),
    ).fetchone()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")
    return company


@app.get("/companies/{company_id}/logo")
def company_logo(company_id: int, connection: Database, admin: SuperAdmin):
    return read_company_logo(company_id, connection)


def read_company_logo(company_id: int, connection):
    company = connection.execute("SELECT logo_data FROM company WHERE id = %s", (company_id,)).fetchone()
    if not company or not company["logo_data"]:
        raise HTTPException(status_code=404, detail="Logo not found.")
    return Response(bytes(company["logo_data"]), media_type="image/webp", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'", "Content-Disposition": 'inline; filename="company-logo.webp"',
    })