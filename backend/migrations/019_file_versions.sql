BEGIN;
ALTER TABLE stored_file ADD COLUMN current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version > 0);
CREATE TABLE file_version (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL,
    company_id BIGINT NOT NULL,
    team_id BIGINT NOT NULL,
    uploader_id BIGINT NOT NULL,
    number INTEGER NOT NULL CHECK (number > 0),
    base_version INTEGER NOT NULL,
    connection_id BIGINT NOT NULL REFERENCES storage_connection(id) ON DELETE RESTRICT,
    object_key TEXT NOT NULL,
    object_version TEXT,
    etag TEXT NOT NULL DEFAULT 'pending',
    name TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    quota_bytes BIGINT NOT NULL CHECK (quota_bytes >= 0),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','ready')),
    upload_key UUID NOT NULL,
    multipart_upload_id TEXT,
    multipart_part_bytes BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    uploaded_at TIMESTAMPTZ,
    UNIQUE (file_id,number),
    UNIQUE (file_id,uploader_id,upload_key),
    FOREIGN KEY (file_id,company_id) REFERENCES stored_file(id,company_id) ON DELETE RESTRICT,
    FOREIGN KEY (team_id,company_id) REFERENCES team(id,company_id) ON DELETE RESTRICT,
    FOREIGN KEY (uploader_id,company_id) REFERENCES team_account(id,company_id) ON DELETE RESTRICT
);
INSERT INTO file_version(file_id,company_id,team_id,uploader_id,number,base_version,connection_id,object_key,object_version,etag,name,size_bytes,quota_bytes,state,upload_key,created_at,uploaded_at)
    SELECT id,company_id,team_id,owner_id,1,1,connection_id,object_key,object_version,etag,name,size_bytes,0,'ready',gen_random_uuid(),created_at,uploaded_at
    FROM stored_file WHERE state IN ('ready','quarantined','trashed');
CREATE FUNCTION enforce_file_version_quota() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE delta BIGINT;
BEGIN
    delta := CASE WHEN TG_OP='INSERT' THEN NEW.quota_bytes ELSE NEW.quota_bytes-OLD.quota_bytes END;
    IF delta <> 0 THEN
        UPDATE team SET storage_used_bytes=storage_used_bytes+delta
        WHERE id=NEW.team_id AND (delta<0 OR (storage_quota_bytes>0 AND storage_used_bytes+delta<=storage_quota_bytes));
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Team storage allocation exceeded or not configured' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER file_version_quota BEFORE INSERT OR UPDATE OF quota_bytes ON file_version
    FOR EACH ROW EXECUTE FUNCTION enforce_file_version_quota();
CREATE TABLE file_activity (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id UUID NOT NULL,
    company_id BIGINT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (file_id,company_id) REFERENCES stored_file(id,company_id) ON DELETE RESTRICT
);
CREATE INDEX file_activity_file_idx ON file_activity(file_id,created_at DESC,id DESC);
COMMIT;