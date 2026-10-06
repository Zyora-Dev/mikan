BEGIN;
CREATE TABLE IF NOT EXISTS workflow (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    team_ids BIGINT[] NOT NULL,
    submitter_roles TEXT[] NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    version INTEGER NOT NULL DEFAULT 1,
    graph JSONB NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (id, company_id)
);
CREATE INDEX IF NOT EXISTS workflow_company_idx ON workflow(company_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS stored_file_id_company_unique ON stored_file(id, company_id);
CREATE TABLE IF NOT EXISTS workflow_run (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id BIGINT NOT NULL,
    workflow_id BIGINT NOT NULL,
    workflow_name TEXT NOT NULL,
    workflow_version INTEGER NOT NULL,
    graph JSONB NOT NULL,
    file_id UUID NOT NULL,
    file_name TEXT NOT NULL,
    file_snapshot JSONB NOT NULL,
    submitter_id BIGINT NOT NULL,
    submitter_team_id BIGINT NOT NULL,
    reviewers JSONB NOT NULL,
    request_key UUID NOT NULL,
    current_node TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'changes_requested', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (workflow_id, company_id) REFERENCES workflow(id, company_id) ON DELETE RESTRICT,
    FOREIGN KEY (file_id, company_id) REFERENCES stored_file(id, company_id) ON DELETE RESTRICT,
    FOREIGN KEY (submitter_id, company_id) REFERENCES team_account(id, company_id) ON DELETE RESTRICT,
    UNIQUE (company_id, submitter_id, request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS workflow_pending_file_idx ON workflow_run(workflow_id, file_id) WHERE status='pending';
CREATE INDEX IF NOT EXISTS workflow_run_company_idx ON workflow_run(company_id, created_at DESC);
CREATE TABLE IF NOT EXISTS workflow_task (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES workflow_run(id) ON DELETE RESTRICT,
    node_id TEXT NOT NULL,
    reviewer_id BIGINT NOT NULL REFERENCES team_account(id) ON DELETE RESTRICT,
    reviewer_team_id BIGINT NOT NULL REFERENCES team(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'changes_requested', 'cancelled')),
    comment TEXT NOT NULL DEFAULT '',
    decided_at TIMESTAMPTZ,
    UNIQUE (run_id, node_id, reviewer_id)
);
CREATE INDEX IF NOT EXISTS workflow_task_inbox_idx ON workflow_task(reviewer_id, status, run_id);
CREATE TABLE IF NOT EXISTS workflow_event (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workflow_id BIGINT NOT NULL REFERENCES workflow(id) ON DELETE RESTRICT,
    run_id UUID REFERENCES workflow_run(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    node_id TEXT,
    actor_name TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS workflow_event_run_idx ON workflow_event(run_id, created_at, id);
CREATE TABLE IF NOT EXISTS workflow_notification (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES workflow_run(id) ON DELETE RESTRICT,
    node_id TEXT NOT NULL,
    recipient_id BIGINT NOT NULL REFERENCES team_account(id) ON DELETE RESTRICT,
    message TEXT NOT NULL,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (run_id, node_id, recipient_id)
);
CREATE INDEX IF NOT EXISTS workflow_notification_inbox_idx ON workflow_notification(recipient_id, created_at DESC);
COMMIT;