BEGIN;

CREATE TABLE admin (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email TEXT NOT NULL CHECK (btrim(email) <> ''),
    name TEXT NOT NULL CHECK (btrim(name) <> ''),
    role TEXT NOT NULL DEFAULT 'super_admin' CHECK (role = 'super_admin'),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX admin_email_unique ON admin (lower(btrim(email)));

COMMIT;