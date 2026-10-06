BEGIN;

ALTER TABLE stored_file
    ADD COLUMN share_token TEXT NOT NULL DEFAULT replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''),
    ADD COLUMN public_access BOOLEAN NOT NULL DEFAULT FALSE,
    ADD CONSTRAINT stored_file_share_token_unique UNIQUE (share_token);

ALTER TABLE file_share
    ADD COLUMN email_status TEXT NOT NULL DEFAULT 'not_requested'
        CHECK (email_status IN ('not_requested', 'queued', 'retry', 'accepted', 'failed', 'cancelled')),
    ADD COLUMN email_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN email_next_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp();
ALTER TABLE file_share ALTER COLUMN email_status SET DEFAULT 'queued';
CREATE INDEX file_share_email_pending ON file_share(email_next_at)
    WHERE email_status IN ('queued', 'retry');

COMMIT;