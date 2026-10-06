import re

from fastapi import HTTPException
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from security import hash_password

ADMIN_COLUMNS = "admin.id, admin.name, admin.email, admin.mobile, admin.company_id, admin.role, admin.is_active, admin.created_at, company.name AS company_name"


class CompanyAdminInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    email: EmailStr = Field(max_length=254)
    mobile: str = Field(min_length=7, max_length=25)
    password: SecretStr = Field(min_length=12, max_length=128)
    company_id: int = Field(gt=0, le=9223372036854775807, strict=True)

    @field_validator("mobile")
    @classmethod
    def valid_mobile(cls, value):
        if not re.fullmatch(r"\+?[0-9 ()-]+", value) or not 7 <= len(re.sub(r"\D", "", value)) <= 15:
            raise ValueError("Enter a valid mobile number with 7-15 digits.")
        return value


class CompanyAdminUpdate(CompanyAdminInput):
    password: SecretStr | None = Field(default=None, min_length=12, max_length=128)
    is_active: bool = Field(default=True, strict=True)


def update_company_admin(connection, identifier, payload):
    encoded = hash_password(payload.password.get_secret_value()) if payload.password else None
    try:
        with connection.transaction():
            updated = connection.execute(
                """UPDATE admin SET name=%s, email=%s, mobile=%s, company_id=%s,
                   password_hash=COALESCE(%s,password_hash), is_active=%s
                   WHERE id=%s AND role='admin' RETURNING id""",
                (payload.name, str(payload.email).lower(), payload.mobile, payload.company_id,
                 encoded, payload.is_active, identifier),
            ).fetchone()
            if not updated:
                raise HTTPException(status_code=404, detail="Company admin not found.")
            connection.execute("DELETE FROM admin_session WHERE admin_id=%s", (identifier,))
    except UniqueViolation as error:
        raise HTTPException(status_code=409, detail="An account with this email already exists.") from error
    except ForeignKeyViolation as error:
        raise HTTPException(status_code=422, detail="Select an existing company.") from error
    return connection.execute(
        f"SELECT {ADMIN_COLUMNS} FROM admin JOIN company ON company.id=admin.company_id WHERE admin.id=%s",
        (identifier,),
    ).fetchone()


def delete_company_admin(connection, identifier):
    try:
        with connection.transaction():
            deleted = connection.execute("DELETE FROM admin WHERE id=%s AND role='admin' RETURNING id", (identifier,)).fetchone()
            if not deleted:
                raise HTTPException(status_code=404, detail="Company admin not found.")
    except ForeignKeyViolation as error:
        raise HTTPException(status_code=409, detail="This admin has dependent records. Deactivate the account instead.") from error
    return {"ok": True}


def create_company_admin(connection, payload):
    if not connection.execute("SELECT id FROM company WHERE id = %s", (payload.company_id,)).fetchone():
        raise HTTPException(status_code=422, detail="Select an existing company.")
    encoded = hash_password(payload.password.get_secret_value())
    try:
        with connection.transaction():
            created = connection.execute(
                """INSERT INTO admin (name, email, mobile, password_hash, company_id, role)
                   VALUES (%s, %s, %s, %s, %s, 'admin') RETURNING id""",
                (payload.name, str(payload.email).lower(), payload.mobile, encoded, payload.company_id),
            ).fetchone()
    except UniqueViolation as error:
        raise HTTPException(status_code=409, detail="An account with this email already exists.") from error
    except ForeignKeyViolation as error:
        raise HTTPException(status_code=422, detail="Select an existing company.") from error
    return connection.execute(
        f"SELECT {ADMIN_COLUMNS} FROM admin JOIN company ON company.id = admin.company_id WHERE admin.id = %s",
        (created["id"],),
    ).fetchone()