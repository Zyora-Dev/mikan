BEGIN;
ALTER TABLE team ADD COLUMN IF NOT EXISTS storage_quota_bytes BIGINT NOT NULL DEFAULT 0 CHECK (storage_quota_bytes BETWEEN 0 AND 9000000000000000);
ALTER TABLE team ADD COLUMN IF NOT EXISTS storage_used_bytes BIGINT NOT NULL DEFAULT 0 CHECK (storage_used_bytes >= 0);
ALTER TABLE team ADD COLUMN IF NOT EXISTS manager_can_view_drives BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE team SET storage_used_bytes = (SELECT COALESCE(sum(size_bytes), 0) FROM stored_file WHERE team_id = team.id);

CREATE OR REPLACE FUNCTION enforce_team_storage_quota() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.team_id = NEW.team_id AND OLD.size_bytes = NEW.size_bytes THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        PERFORM id FROM team WHERE id IN (OLD.team_id, NEW.team_id) ORDER BY id FOR UPDATE;
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        UPDATE team SET storage_used_bytes = storage_used_bytes - OLD.size_bytes WHERE id = OLD.team_id;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        UPDATE team SET storage_used_bytes = storage_used_bytes + NEW.size_bytes
        WHERE id = NEW.team_id AND storage_quota_bytes > 0
        AND storage_used_bytes + NEW.size_bytes <= storage_quota_bytes;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Team storage allocation exceeded or not configured' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END;
$$;
DROP TRIGGER IF EXISTS stored_file_team_quota ON stored_file;
CREATE TRIGGER stored_file_team_quota BEFORE INSERT OR UPDATE OR DELETE ON stored_file
FOR EACH ROW EXECUTE FUNCTION enforce_team_storage_quota();
COMMIT;