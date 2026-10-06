BEGIN;
CREATE TABLE IF NOT EXISTS team_folder_activity (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id BIGINT NOT NULL,
    team_id BIGINT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('created', 'tracking_started', 'settings_changed')),
    actor_id BIGINT REFERENCES admin(id) ON DELETE SET NULL,
    actor_name TEXT,
    previous_quota_bytes BIGINT,
    quota_bytes BIGINT,
    previous_manager_access BOOLEAN,
    manager_access BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (team_id, company_id) REFERENCES team(id, company_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS team_folder_activity_list_idx ON team_folder_activity(company_id, team_id, created_at DESC, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS team_folder_activity_initial_idx ON team_folder_activity(team_id, kind) WHERE kind IN ('created', 'tracking_started');
INSERT INTO team_folder_activity (company_id, team_id, kind, created_at)
SELECT company_id, id, 'created', created_at FROM team ON CONFLICT DO NOTHING;
INSERT INTO team_folder_activity (company_id, team_id, kind, quota_bytes, manager_access)
SELECT company_id, id, 'tracking_started', storage_quota_bytes, manager_can_view_drives FROM team ON CONFLICT DO NOTHING;
COMMIT;