BEGIN;
CREATE UNIQUE INDEX IF NOT EXISTS team_account_id_company_unique ON team_account(id, company_id);
CREATE TABLE IF NOT EXISTS stored_file (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    team_id BIGINT NOT NULL,
    owner_id BIGINT NOT NULL,
    connection_id BIGINT NOT NULL REFERENCES storage_connection(id) ON DELETE RESTRICT,
    object_key TEXT NOT NULL CHECK (octet_length(object_key) BETWEEN 1 AND 1024),
    object_version TEXT,
    etag TEXT NOT NULL CHECK (length(etag) BETWEEN 1 AND 256),
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 255),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'ready', 'quarantined', 'trashed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (team_id, company_id) REFERENCES team(id, company_id) ON DELETE RESTRICT,
    FOREIGN KEY (owner_id, company_id) REFERENCES team_account(id, company_id) ON DELETE RESTRICT,
    UNIQUE (connection_id, object_key)
);
CREATE INDEX IF NOT EXISTS stored_file_owner_idx ON stored_file(company_id, team_id, owner_id, created_at);
COMMIT;