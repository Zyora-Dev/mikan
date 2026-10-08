BEGIN;
ALTER TABLE company_root_entry
    DROP CONSTRAINT company_root_entry_parent_check,
    DROP CONSTRAINT company_root_entry_path_check,
    ADD CONSTRAINT company_root_entry_parent_check CHECK (length(parent) <= 512),
    ADD CONSTRAINT company_root_entry_path_check CHECK (length(path) <= 512);
ALTER TABLE zoho_migration_job
    DROP CONSTRAINT zoho_migration_job_destination_path_check,
    ADD CONSTRAINT zoho_migration_job_destination_path_check CHECK (length(destination_path) BETWEEN 1 AND 512);
ALTER TABLE zoho_migration_item
    DROP CONSTRAINT zoho_migration_item_destination_path_check,
    ADD CONSTRAINT zoho_migration_item_destination_path_check CHECK (length(destination_path) BETWEEN 1 AND 512);
COMMIT;