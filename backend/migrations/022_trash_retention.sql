BEGIN;
CREATE TABLE company_trash_policy (
    company_id BIGINT PRIMARY KEY REFERENCES company(id) ON DELETE RESTRICT,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    retention_days INTEGER NOT NULL DEFAULT 30 CHECK (retention_days BETWEEN 1 AND 3650),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE stored_file ADD COLUMN trashed_at TIMESTAMPTZ;
ALTER TABLE stored_file ADD COLUMN trashed_by_owner BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE stored_file ADD COLUMN purged_at TIMESTAMPTZ;
ALTER TABLE stored_file ADD COLUMN purge_next_attempt_at TIMESTAMPTZ;
ALTER TABLE stored_file ADD COLUMN purge_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE stored_file ADD COLUMN purge_error TEXT;
ALTER TABLE stored_file DROP CONSTRAINT stored_file_state_check;
ALTER TABLE stored_file ADD CONSTRAINT stored_file_state_check CHECK (state IN ('pending','ready','quarantined','trashed','cancelling','cancelled','purging','purged'));
UPDATE stored_file SET trashed_at=COALESCE(admin_trashed_at,clock_timestamp()) WHERE state='trashed';
CREATE FUNCTION track_file_trash() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state='trashed' AND (TG_OP='INSERT' OR OLD.state<>'trashed') THEN
        NEW.trashed_at := clock_timestamp();
    ELSIF NEW.state='ready' THEN
        NEW.trashed_at := NULL;
        NEW.trashed_by_owner := FALSE;
        NEW.purge_next_attempt_at := NULL;
        NEW.purge_error := NULL;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER track_file_trash BEFORE INSERT OR UPDATE OF state ON stored_file FOR EACH ROW EXECUTE FUNCTION track_file_trash();
CREATE INDEX stored_file_trash_due_idx ON stored_file(trashed_at,purge_next_attempt_at) WHERE state IN ('trashed','purging');
CREATE OR REPLACE FUNCTION enforce_team_storage_quota() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_charge BIGINT := 0; new_charge BIGINT := 0;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_charge := CASE WHEN OLD.state IN ('cancelled','purged') THEN 0 ELSE OLD.size_bytes END;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_charge := CASE WHEN NEW.state IN ('cancelled','purged') THEN 0 ELSE NEW.size_bytes END;
    END IF;
    IF TG_OP='UPDATE' AND OLD.team_id=NEW.team_id AND old_charge=new_charge THEN
        RETURN NEW;
    END IF;
    IF TG_OP='UPDATE' THEN
        PERFORM id FROM team WHERE id IN (OLD.team_id,NEW.team_id) ORDER BY id FOR UPDATE;
    END IF;
    IF TG_OP <> 'INSERT' THEN
        UPDATE team SET storage_used_bytes=storage_used_bytes-old_charge WHERE id=OLD.team_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        IF NEW.state NOT IN ('cancelled','purged') THEN
            UPDATE team SET storage_used_bytes=storage_used_bytes+new_charge
            WHERE id=NEW.team_id AND storage_quota_bytes>0 AND storage_used_bytes+new_charge<=storage_quota_bytes;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Team storage allocation exceeded or not configured' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END;
$$;
COMMIT;