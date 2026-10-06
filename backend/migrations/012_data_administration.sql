BEGIN;
CREATE TABLE IF NOT EXISTS data_folder (
    id BIGSERIAL PRIMARY KEY,
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    team_id BIGINT NOT NULL,
    owner_id BIGINT NOT NULL,
    path TEXT NOT NULL CHECK (length(path) BETWEEN 1 AND 255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (team_id, company_id) REFERENCES team(id, company_id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, company_id) REFERENCES team_account(id, company_id) ON DELETE RESTRICT,
    UNIQUE(company_id, team_id, owner_id, path)
);
ALTER TABLE stored_file ADD COLUMN IF NOT EXISTS admin_trashed_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS data_activity (
    id BIGSERIAL PRIMARY KEY,
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS data_activity_company_idx ON data_activity(company_id, created_at DESC, id DESC);
COMMIT;