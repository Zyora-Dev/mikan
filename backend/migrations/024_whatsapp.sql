BEGIN;
CREATE TABLE IF NOT EXISTS whatsapp_integration (
    id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    phone_number_id TEXT NOT NULL,
    business_id TEXT NOT NULL,
    token_encrypted TEXT NOT NULL,
    updated_by BIGINT REFERENCES admin(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS whatsapp_consent (
    id BIGSERIAL PRIMARY KEY,
    company_name TEXT NOT NULL,
    recipient TEXT NOT NULL CHECK (recipient ~ '^\+[1-9][0-9]{7,14}$'),
    source TEXT NOT NULL,
    recorded_by BIGINT REFERENCES admin(id) ON DELETE SET NULL,
    consented_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    last_status TEXT CHECK (last_status IN ('accepted', 'failed', 'unknown')),
    last_message_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_active_recipient ON whatsapp_consent (recipient) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS whatsapp_consent_date ON whatsapp_consent (consented_at DESC, id DESC);
COMMIT;