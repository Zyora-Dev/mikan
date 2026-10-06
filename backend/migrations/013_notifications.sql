BEGIN;
ALTER TABLE team ADD COLUMN IF NOT EXISTS storage_alerts_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE team ADD COLUMN IF NOT EXISTS storage_warning_percent INTEGER NOT NULL DEFAULT 80 CHECK (storage_warning_percent BETWEEN 1 AND 98);
ALTER TABLE team ADD COLUMN IF NOT EXISTS storage_critical_percent INTEGER NOT NULL DEFAULT 90 CHECK (storage_critical_percent BETWEEN 2 AND 99 AND storage_critical_percent > storage_warning_percent);

CREATE TABLE IF NOT EXISTS notification (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    team_id BIGINT NOT NULL REFERENCES team(id) ON DELETE RESTRICT,
    admin_id BIGINT REFERENCES admin(id) ON DELETE CASCADE,
    account_id BIGINT REFERENCES team_account(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('storage_warning', 'storage_critical', 'storage_full')),
    message TEXT NOT NULL,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (num_nonnulls(admin_id, account_id) = 1)
);
CREATE INDEX IF NOT EXISTS notification_admin_idx ON notification(admin_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS notification_account_idx ON notification(account_id, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION storage_alert_level(used_bytes BIGINT, quota_bytes BIGINT, enabled BOOLEAN, warning_percent INTEGER, critical_percent INTEGER)
RETURNS INTEGER LANGUAGE SQL IMMUTABLE AS $$
    SELECT CASE
        WHEN NOT enabled OR quota_bytes = 0 THEN 0
        WHEN used_bytes >= quota_bytes THEN 3
        WHEN used_bytes::numeric * 100 >= quota_bytes::numeric * critical_percent THEN 2
        WHEN used_bytes::numeric * 100 >= quota_bytes::numeric * warning_percent THEN 1
        ELSE 0 END;
$$;

CREATE OR REPLACE FUNCTION notify_storage_threshold() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    previous_level INTEGER;
    current_level INTEGER;
    notice_kind TEXT;
    notice_message TEXT;
    company_name TEXT;
BEGIN
    previous_level := storage_alert_level(OLD.storage_used_bytes, OLD.storage_quota_bytes, OLD.storage_alerts_enabled, OLD.storage_warning_percent, OLD.storage_critical_percent);
    current_level := storage_alert_level(NEW.storage_used_bytes, NEW.storage_quota_bytes, NEW.storage_alerts_enabled, NEW.storage_warning_percent, NEW.storage_critical_percent);
    IF current_level <= previous_level THEN RETURN NEW; END IF;
    notice_kind := CASE current_level WHEN 3 THEN 'storage_full' WHEN 2 THEN 'storage_critical' ELSE 'storage_warning' END;
    SELECT name INTO company_name FROM company WHERE id = NEW.company_id;
    notice_message := format('%s / %s: storage is %s%% used or reserved (%s of %s bytes).', company_name, NEW.name,
        floor(NEW.storage_used_bytes::numeric * 100 / NEW.storage_quota_bytes), NEW.storage_used_bytes, NEW.storage_quota_bytes);
    INSERT INTO notification(company_id, team_id, admin_id, kind, message)
        SELECT NEW.company_id, NEW.id, id, notice_kind, notice_message FROM admin
        WHERE is_active AND (role = 'super_admin' OR (role = 'admin' AND company_id = NEW.company_id));
    INSERT INTO notification(company_id, team_id, account_id, kind, message)
        SELECT NEW.company_id, NEW.id, id, notice_kind, notice_message FROM team_account
        WHERE status = 'active' AND company_id = NEW.company_id AND team_id = NEW.id;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS team_storage_alert ON team;
CREATE TRIGGER team_storage_alert AFTER UPDATE OF storage_used_bytes, storage_quota_bytes, storage_alerts_enabled, storage_warning_percent, storage_critical_percent
    ON team FOR EACH ROW EXECUTE FUNCTION notify_storage_threshold();
COMMIT;