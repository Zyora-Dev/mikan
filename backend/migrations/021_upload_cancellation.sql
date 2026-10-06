BEGIN;
ALTER TABLE stored_file DROP CONSTRAINT stored_file_state_check;
ALTER TABLE stored_file ADD CONSTRAINT stored_file_state_check CHECK (state IN ('pending','ready','quarantined','trashed','cancelling','cancelled'));
ALTER TABLE file_version DROP CONSTRAINT file_version_state_check;
ALTER TABLE file_version ADD CONSTRAINT file_version_state_check CHECK (state IN ('pending','ready','cancelling','cancelled'));
CREATE OR REPLACE FUNCTION enforce_team_storage_quota() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_charge BIGINT := 0; new_charge BIGINT := 0;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_charge := CASE WHEN OLD.state='cancelled' THEN 0 ELSE OLD.size_bytes END;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_charge := CASE WHEN NEW.state='cancelled' THEN 0 ELSE NEW.size_bytes END;
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
        IF NEW.state <> 'cancelled' THEN
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