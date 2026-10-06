# Render Deployment

## Prepared Configuration

The root `render.yaml` defines a paid Next.js web service, a private FastAPI
service and private PostgreSQL 16, all in Singapore. Each starts with 1 CPU and
2 GB RAM; PostgreSQL has 20 GB of metadata storage. Review availability and the
cost summary in Render before creating resources. These are starting sizes,
not a capacity guarantee. Confirm credit eligibility and expiry in your account;
credits do not eliminate bandwidth, storage or other usage charges.

Files remain in private R2/S3 buckets configured through Super Admin. The app's
16 TB company quota does not provision 16 TB in Render or either object store.
External storage charges are separate from Render credits. Photos and logos are
in PostgreSQL. Temporary upload chunks can use ephemeral disk, but concurrency,
available scratch space and request limits must be tested before launch.

Auto-deploy and preview environments are off. No cloud resources have been
created. The backend's existing automation worker starts disabled deliberately:
it can send queued emails/webhooks and permanently clean eligible Trash.

## Before Creating Services

1. Decide whether this is a fresh installation or a migration of local data.
   Never point an empty production database at existing user objects and assume
   it can reconstruct their ownership or file metadata.
2. Choose the canonical HTTPS frontend origin (no trailing slash). Set backend
   `ADMIN_ORIGINS` to this single origin. The Blueprint shares it with frontend
   `ADMIN_PUBLIC_ORIGIN` and backend `TEAM_PUBLIC_ORIGIN`. Do not enter multiple
   comma-separated origins in this Blueprint. Add the custom domain in Render
   and follow its DNS instructions; no domain has been assumed here.
3. Supply `INTEGRATION_ENCRYPTION_KEY` privately in Render. For imported data,
   use the SAME key that encrypted the existing integration records. Back up
   the database and key together, separately from source control. Do not copy
   local `.env` files into production or paste secrets into chat.
4. For a genuinely fresh database only, generate a new Fernet-compatible key
   in your own terminal and store the output directly in Render/password vault:

   ```sh
   node -e "console.log(require('node:crypto').randomBytes(32).toString('base64').replaceAll('+', '-').replaceAll('/', '_'))"
   ```

5. Establish trusted client-IP ingress before enabling team sign-in. Set
   `TEAM_CLIENT_IP_HEADER` to a lowercase header that ingress ALWAYS overwrites
   with one verified IP. The current proxy rejects comma-separated chains.
   Do not guess `x-forwarded-for` or trust a caller-supplied header. Verify Render's
   actual header contract and test spoofed headers; if no suitable guaranteed
   header is available, a trusted ingress adapter is required before launch.
   Adding a separate proxy also requires preventing requests that bypass it.

The Blueprint generates and shares `TEAM_PROXY_SECRET` automatically. Neither
that secret nor the encryption key belongs in a `NEXT_PUBLIC_*` variable.
Linked environment values refresh on Blueprint sync, not immediately when their
source changes. After changing the origin or rotating the shared proxy secret,
sync and redeploy both services and confirm their values match. `sync: false`
prompts appear only during initial creation; later changes are made in Render.
The frontend constructs `ADMIN_API_URL` at startup from Render's private backend
host/port. Backend and database have no public ingress. Keep database external
access disabled and use the direct database URL, not transaction pooling, because
notifications and uploads use PostgreSQL LISTEN and session advisory locks.

## Create And Initialize

After an approved commit/push, connect the repository using Render's
**New > Blueprint**, select `render.yaml`, supply the requested values and review
the paid-resource summary before applying. Use distinct names if these resource
names already exist: Blueprint sync can update matching resources.

The initial frontend can render sign-in before the database is initialized.
This is NOT a readiness check for database-backed functionality. No migration
runs automatically and no accounts are seeded. Keep the site unannounced until
initialization and all checks below pass.

For a NEW EMPTY database only, open the backend's Render Shell (working directory
is `backend`) and run this guarded initialization. It stops if public tables
already exist; do not bypass that guard for a restore or partially applied setup.

```sh
set -eu
export PGDATABASE="$DATABASE_URL"
tables=$(psql -X -v ON_ERROR_STOP=1 -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
test "$tables" = 0
psql -X -v ON_ERROR_STOP=1 -f schema.sql
for migration in migrations/[0-9][0-9][0-9]_*.sql; do
  psql -X -v ON_ERROR_STOP=1 -f "$migration"
done
```

This uses the database's application owner so new objects are accessible to it.
If initialization fails, stop and inspect the last migration; do not rerun the
base schema or reset the database. Fresh-install execution on Render remains
unverified. Existing installations must apply only unapplied migrations after
backup and review; there is no automatic migration ledger in this repository.

For a migration of current data, instead take a consistent PostgreSQL backup,
restore into a separate empty target with appropriate ownership/privileges, and
retain the original encryption key and storage objects. Do not run the fresh
initialization block first. Plan write cutover and verify restoration before
retiring the source. No local data transfer has been authorized or performed.

For a fresh installation, create the first Super Admin in the backend Render
Shell. Replace the example identity; the command prompts privately for a password:

```sh
python create_super_admin.py --email "admin@example.com" --name "Administrator"
```

After confirming the schema, integration settings and intended queued work,
change `MIKAN_AUTOMATION_ENABLED` to `'true'` in `render.yaml` and sync/redeploy
the backend. Until then, queued sharing emails, scheduled automation and Trash
cleanup do not run. Keep one backend instance/worker initially. Review worker
coordination and overlapping-deploy behaviour before scaling.

## Launch Checks

- Run `render blueprints validate render.yaml` using Render CLI 2.7+ before
  resource creation. Local YAML parsing and editor checks are not equivalent to
  Render account-level validation. The local installed Ajv cannot compile the
  published JSON Schema2020-12; full schema validation has not passed here.
- Verify the pinned Python version and dependencies install in Render's Linux
  runtime. Run a clean frontend production build; old local builds do not cover
  recent feature additions. PostgreSQL 16 needs a fresh-install/restore test;
  local development currently uses PostgreSQL 14.
- Confirm HTTPS, secure cookies, exact origins, private API/database access,
  trusted client-IP anti-spoofing and successful sign-in for all three roles.
- Verify consented WhatsApp sending, invitations/OTP, workflows and notifications
  with authorized test accounts. API acceptance does not confirm delivery.
- Test actual upload/download/multipart resume/cancel/version/Trash operations,
  byte ranges and SSE through Render ingress. Do not cache authenticated pages,
  private file responses or API responses at a CDN. Validate request timeouts,
  scratch space and bandwidth costs under representative file sizes/load; a
  multi-terabyte product limit is not proof that this hosting path supports it.
- Confirm PostgreSQL backups/PITR retention for the chosen plan and perform an
  isolated restore drill with the encryption key. Preserve private bucket policy
  and appropriate versioning/retention independently of database backups.
- Deploy manually at first. Before each later schema change, back up and review
  compatibility with the still-running release. Application rollback does not
  undo database migrations or external file deletions.

## References

- https://render.com/docs/blueprint-spec
- https://render.com/docs/web-services
- https://render.com/docs/native-runtimes
- https://render.com/schema/render.yaml.json