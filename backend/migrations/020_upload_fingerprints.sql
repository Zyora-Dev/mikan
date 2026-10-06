BEGIN;
ALTER TABLE stored_file ADD COLUMN upload_fingerprint TEXT CHECK (upload_fingerprint ~ '^[a-f0-9]{64}$');
ALTER TABLE file_version ADD COLUMN upload_fingerprint TEXT CHECK (upload_fingerprint ~ '^[a-f0-9]{64}$');
COMMIT;