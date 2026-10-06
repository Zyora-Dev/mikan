BEGIN;
ALTER TABLE stored_file ADD COLUMN IF NOT EXISTS folder TEXT NOT NULL DEFAULT '';
ALTER TABLE stored_file ADD COLUMN IF NOT EXISTS upload_key UUID;
ALTER TABLE stored_file ADD COLUMN IF NOT EXISTS uploaded_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS stored_file_upload_key_idx ON stored_file(company_id, owner_id, upload_key);
CREATE TABLE IF NOT EXISTS company_upload_policy (
    company_id BIGINT PRIMARY KEY REFERENCES company(id) ON DELETE RESTRICT,
    connection_id BIGINT NOT NULL REFERENCES storage_connection(id) ON DELETE RESTRICT,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    max_file_bytes BIGINT NOT NULL DEFAULT 104857600 CHECK (max_file_bytes BETWEEN 1 AND 104857600),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS automation_job (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    workflow_id BIGINT NOT NULL,
    run_id UUID REFERENCES workflow_run(id) ON DELETE RESTRICT,
    file_id UUID,
    kind TEXT NOT NULL CHECK (kind IN ('trigger','email','webhook')),
    dedupe_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','retry','done','failed','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (workflow_id, company_id) REFERENCES workflow(id, company_id) ON DELETE RESTRICT,
    FOREIGN KEY (file_id, company_id) REFERENCES stored_file(id, company_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS automation_job_due_idx ON automation_job(next_at) WHERE status IN ('queued','retry');
CREATE INDEX IF NOT EXISTS automation_job_company_idx ON automation_job(company_id,created_at DESC);
COMMIT;