BEGIN;
ALTER TABLE file_share ADD COLUMN permission TEXT NOT NULL DEFAULT 'view'
    CHECK (permission IN ('view', 'edit'));
COMMIT;