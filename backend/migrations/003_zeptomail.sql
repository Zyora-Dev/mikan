BEGIN;
CREATE TABLE IF NOT EXISTS zeptomail_integration (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    endpoint TEXT NOT NULL,
    sender_email TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    token_encrypted TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by BIGINT REFERENCES admin(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_test_at TIMESTAMPTZ,
    last_test_status TEXT CHECK (last_test_status IN ('accepted', 'failed'))
);
COMMIT;