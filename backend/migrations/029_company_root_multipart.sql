BEGIN;
ALTER TABLE company_root_entry
    ADD COLUMN multipart_upload_id TEXT,
    ADD COLUMN multipart_part_bytes BIGINT CHECK (multipart_part_bytes BETWEEN 16777216 AND 5000000000);
ALTER TABLE company_root_version
    ADD COLUMN multipart_upload_id TEXT,
    ADD COLUMN multipart_part_bytes BIGINT CHECK (multipart_part_bytes BETWEEN 16777216 AND 5000000000);
COMMIT;