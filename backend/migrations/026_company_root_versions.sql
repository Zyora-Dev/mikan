BEGIN;
ALTER TABLE company_root_entry ADD CONSTRAINT company_root_entry_id_company_unique UNIQUE (id,company_id);
CREATE TABLE company_root_version (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL,
    company_id BIGINT NOT NULL,
    source_version_id TEXT NOT NULL,
    version_label TEXT NOT NULL CHECK (length(version_label) BETWEEN 1 AND 100),
    source_created_at TEXT,
    source_modified_at TEXT,
    connection_id BIGINT NOT NULL REFERENCES storage_connection(id) ON DELETE RESTRICT,
    object_key TEXT NOT NULL,
    object_version TEXT,
    etag TEXT NOT NULL DEFAULT 'pending',
    sha256 TEXT CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 255),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','ready')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    uploaded_at TIMESTAMPTZ,
    FOREIGN KEY (file_id,company_id) REFERENCES company_root_entry(id,company_id) ON DELETE RESTRICT,
    CHECK (state='pending' OR (etag<>'pending' AND sha256 IS NOT NULL AND uploaded_at IS NOT NULL)),
    UNIQUE (file_id,source_version_id),
    UNIQUE (connection_id,object_key)
);
CREATE INDEX company_root_version_file_idx ON company_root_version(file_id,created_at DESC,id DESC);
COMMIT;