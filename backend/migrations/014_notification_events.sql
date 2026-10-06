BEGIN;
ALTER TABLE notification ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES workflow_run(id) ON DELETE RESTRICT;
ALTER TABLE notification ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE notification DROP CONSTRAINT IF EXISTS notification_kind_check;
ALTER TABLE notification ADD CONSTRAINT notification_kind_check CHECK (kind IN ('storage_warning', 'storage_critical', 'storage_full', 'workflow', 'automation_failure'));
CREATE UNIQUE INDEX IF NOT EXISTS notification_admin_event_idx ON notification(admin_id, event_key);
CREATE UNIQUE INDEX IF NOT EXISTS notification_account_event_idx ON notification(account_id, event_key);

CREATE OR REPLACE FUNCTION broadcast_notification_change() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    recipient TEXT;
BEGIN
    IF TG_TABLE_NAME = 'workflow_notification' THEN
        recipient := 'team:' || NEW.recipient_id;
    ELSE
        recipient := CASE WHEN NEW.admin_id IS NOT NULL THEN 'admin:' || NEW.admin_id ELSE 'team:' || NEW.account_id END;
    END IF;
    PERFORM pg_notify('mikan_notifications', recipient);
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS notification_change ON notification;
CREATE TRIGGER notification_change AFTER INSERT OR UPDATE ON notification
    FOR EACH ROW EXECUTE FUNCTION broadcast_notification_change();
DROP TRIGGER IF EXISTS workflow_notification_change ON workflow_notification;
CREATE TRIGGER workflow_notification_change AFTER INSERT OR UPDATE ON workflow_notification
    FOR EACH ROW EXECUTE FUNCTION broadcast_notification_change();
COMMIT;