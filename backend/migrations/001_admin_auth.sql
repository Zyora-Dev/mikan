BEGIN;

ALTER TABLE admin ADD COLUMN IF NOT EXISTS password_hash TEXT;

CREATE TABLE IF NOT EXISTS admin_session (
    token_hash TEXT PRIMARY KEY,
    admin_id BIGINT NOT NULL REFERENCES admin(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS admin_session_admin_id_idx ON admin_session (admin_id);

CREATE TABLE IF NOT EXISTS admin_login_attempt (
    email TEXT PRIMARY KEY,
    failures INTEGER NOT NULL DEFAULT 0,
    window_started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMIT;