BEGIN;
CREATE TABLE IF NOT EXISTS storage_connection (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider IN ('r2', 's3')),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    bucket TEXT NOT NULL,
    region TEXT NOT NULL,
    account_id TEXT,
    endpoint TEXT NOT NULL,
    access_key_encrypted TEXT NOT NULL,
    secret_key_encrypted TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by BIGINT REFERENCES admin(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_test_at TIMESTAMPTZ,
    last_test_status TEXT CHECK (last_test_status IN ('passed', 'failed', 'cleanup_failed')),
    UNIQUE (provider, endpoint, bucket)
);
ALTER TABLE storage_connection ADD COLUMN IF NOT EXISTS last_cleanup JSONB;
COMMIT;