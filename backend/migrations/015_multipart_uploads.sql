BEGIN;
ALTER TABLE company_upload_policy DROP CONSTRAINT IF EXISTS company_upload_policy_max_file_bytes_check;
ALTER TABLE company_upload_policy ADD CONSTRAINT company_upload_policy_max_file_bytes_check
    CHECK (max_file_bytes BETWEEN 1 AND 4398046511104);
ALTER TABLE company_upload_policy ALTER COLUMN max_file_bytes SET DEFAULT 4398046511104;
ALTER TABLE stored_file ADD COLUMN IF NOT EXISTS multipart_upload_id TEXT;
ALTER TABLE stored_file ADD COLUMN IF NOT EXISTS multipart_part_bytes BIGINT
    CHECK (multipart_part_bytes BETWEEN 16777216 AND 5000000000);
COMMIT;