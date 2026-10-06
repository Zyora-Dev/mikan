BEGIN;
CREATE UNIQUE INDEX IF NOT EXISTS stored_file_id_company_unique ON stored_file(id, company_id);
CREATE TABLE IF NOT EXISTS file_share (
    file_id UUID NOT NULL,
    company_id BIGINT NOT NULL,
    recipient_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (file_id, recipient_id),
    FOREIGN KEY (file_id, company_id) REFERENCES stored_file(id, company_id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id, company_id) REFERENCES team_account(id, company_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS file_share_recipient_idx ON file_share(company_id, recipient_id, created_at DESC);
COMMIT;