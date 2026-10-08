BEGIN;
CREATE TABLE company_root_entry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('folder','file')),
    parent TEXT NOT NULL DEFAULT '' CHECK (length(parent) <= 255),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 255 AND name NOT IN ('.','..') AND strpos(name,'/')=0),
    path TEXT GENERATED ALWAYS AS (CASE WHEN parent='' THEN name ELSE parent || '/' || name END) STORED,
    source_id TEXT,
    source_modified_at TEXT,
    size_bytes BIGINT NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    connection_id BIGINT REFERENCES storage_connection(id) ON DELETE RESTRICT,
    object_key TEXT,
    object_version TEXT,
    etag TEXT,
    sha256 TEXT CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    state TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('pending','ready')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    uploaded_at TIMESTAMPTZ,
    CHECK (length(path) <= 255),
    CHECK ((kind='folder' AND size_bytes=0 AND state='ready' AND connection_id IS NULL AND object_key IS NULL)
        OR (kind='file' AND connection_id IS NOT NULL AND object_key IS NOT NULL AND source_id IS NOT NULL)),
    CHECK (kind='folder' OR state='pending' OR (etag IS NOT NULL AND sha256 IS NOT NULL AND uploaded_at IS NOT NULL)),
    UNIQUE (company_id,path),
    UNIQUE (company_id,source_id),
    UNIQUE (connection_id,object_key)
);
CREATE INDEX company_root_parent_idx ON company_root_entry(company_id,parent,kind,name);
COMMIT;