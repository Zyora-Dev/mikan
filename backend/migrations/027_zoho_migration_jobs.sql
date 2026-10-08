BEGIN;
CREATE TABLE zoho_migration_job (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    source_root_id TEXT NOT NULL,
    source_folder_id TEXT NOT NULL,
    source_folder_name TEXT NOT NULL CHECK (length(source_folder_name) BETWEEN 1 AND 255),
    destination_path TEXT NOT NULL CHECK (length(destination_path) BETWEEN 1 AND 255),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','inventory','transferring','verifying','complete','failed')),
    phase TEXT NOT NULL DEFAULT 'Queued',
    folders_total BIGINT NOT NULL DEFAULT 0 CHECK (folders_total >= 0),
    folders_complete BIGINT NOT NULL DEFAULT 0 CHECK (folders_complete BETWEEN 0 AND folders_total),
    files_total BIGINT NOT NULL DEFAULT 0 CHECK (files_total >= 0),
    files_complete BIGINT NOT NULL DEFAULT 0 CHECK (files_complete BETWEEN 0 AND files_total),
    versions_total BIGINT NOT NULL DEFAULT 0 CHECK (versions_total >= 0),
    versions_complete BIGINT NOT NULL DEFAULT 0 CHECK (versions_complete BETWEEN 0 AND versions_total),
    bytes_total BIGINT NOT NULL DEFAULT 0 CHECK (bytes_total >= 0),
    bytes_complete BIGINT NOT NULL DEFAULT 0 CHECK (bytes_complete BETWEEN 0 AND bytes_total),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (company_id,source_root_id,source_folder_id),
    UNIQUE (company_id,destination_path)
);
CREATE INDEX zoho_migration_job_worker_idx ON zoho_migration_job(status,updated_at,id);

CREATE TABLE zoho_migration_item (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES zoho_migration_job(id) ON DELETE RESTRICT,
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('folder','file','version')),
    source_id TEXT NOT NULL,
    source_parent_id TEXT,
    source_file_id TEXT,
    source_version_id TEXT,
    source_name TEXT NOT NULL CHECK (length(source_name) BETWEEN 1 AND 255),
    destination_path TEXT NOT NULL CHECK (length(destination_path) BETWEEN 1 AND 255),
    version_label TEXT,
    size_bytes BIGINT NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    source_created_at TEXT,
    source_modified_at TEXT,
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    destination_entry_id UUID,
    destination_version_id UUID,
    sha256 TEXT CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','transferring','ready','failed')),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (job_id,kind,source_id),
    UNIQUE (job_id,destination_path,kind,source_version_id)
);
CREATE INDEX zoho_migration_item_pending_idx ON zoho_migration_item(job_id,state,kind,destination_path);
COMMIT;