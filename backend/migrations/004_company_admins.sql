BEGIN;
ALTER TABLE admin ADD COLUMN IF NOT EXISTS mobile TEXT;
ALTER TABLE admin ADD COLUMN IF NOT EXISTS company_id BIGINT REFERENCES company(id) ON DELETE RESTRICT;
ALTER TABLE admin DROP CONSTRAINT IF EXISTS admin_role_check;
ALTER TABLE admin ADD CONSTRAINT admin_role_check CHECK (role IN ('super_admin', 'admin'));
ALTER TABLE admin DROP CONSTRAINT IF EXISTS admin_company_check;
ALTER TABLE admin ADD CONSTRAINT admin_company_check CHECK (
    (role = 'super_admin' AND company_id IS NULL) OR
    (role = 'admin' AND company_id IS NOT NULL AND mobile IS NOT NULL
     AND length(btrim(mobile)) BETWEEN 7 AND 25 AND password_hash IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS admin_company_idx ON admin(company_id);
COMMIT;