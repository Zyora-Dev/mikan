BEGIN;
CREATE TABLE IF NOT EXISTS team (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (id, company_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS team_name_unique ON team(company_id, lower(btrim(name)));
CREATE TABLE IF NOT EXISTS team_account (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id BIGINT NOT NULL REFERENCES company(id) ON DELETE RESTRICT,
    team_id BIGINT NOT NULL,
    name TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
    email TEXT NOT NULL,
    mobile TEXT NOT NULL CHECK (length(btrim(mobile)) BETWEEN 7 AND 25),
    role TEXT NOT NULL CHECK (role IN ('manager', 'member')),
    status TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'active', 'disabled')),
    auth_type TEXT CHECK (auth_type IN ('otp', 'password')),
    password_hash TEXT,
    activated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (team_id, company_id) REFERENCES team(id, company_id) ON DELETE RESTRICT,
    CHECK ((auth_type IS NULL AND password_hash IS NULL AND status <> 'active') OR
           (auth_type = 'otp' AND password_hash IS NULL) OR
           (auth_type = 'password' AND password_hash IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS team_account_email_unique ON team_account(lower(btrim(email)));
CREATE UNIQUE INDEX IF NOT EXISTS team_one_manager ON team_account(team_id) WHERE role = 'manager' AND status <> 'disabled';
CREATE INDEX IF NOT EXISTS team_account_company_idx ON team_account(company_id, created_at);
CREATE TABLE IF NOT EXISTS team_verification (
    token_hash TEXT PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES team_account(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('activate', 'recover')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (account_id, purpose)
);
CREATE TABLE IF NOT EXISTS team_otp (
    challenge_hash TEXT PRIMARY KEY,
    account_id BIGINT NOT NULL UNIQUE REFERENCES team_account(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0 CHECK (failures BETWEEN 0 AND 5)
);
CREATE TABLE IF NOT EXISTS team_session (
    token_hash TEXT PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES team_account(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS team_session_expiry_idx ON team_session(expires_at);
CREATE TABLE IF NOT EXISTS team_rate_limit (
    key TEXT PRIMARY KEY,
    hits INTEGER NOT NULL DEFAULT 0,
    window_started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
COMMIT;