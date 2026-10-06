BEGIN;

ALTER TABLE team_account ADD COLUMN IF NOT EXISTS photo_data bytea;

COMMIT;