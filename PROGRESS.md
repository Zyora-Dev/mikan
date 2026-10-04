# Mikan Progress

## Working Rules
- Record completed changes and verification here.
- Freeze backend/requirements.txt from the existing .venv after every package installation and verify dependency consistency.
- Never delete or recreate existing databases to apply schema changes.

## 2026-10-04
- Installed FastAPI 0.142.2 and Uvicorn 0.54.0 with standard dependencies in the workspace-root .venv (Python 3.13.14).
- Verified an in-memory FastAPI request successfully.
- Selected PostgreSQL for the local mikan database; PostgreSQL 14.19 is available through the local redfoxhotels account.
- Installed psycopg 3.3.6 with its binary package and pinned all installed dependencies in backend/requirements.txt.
- Added backend/schema.sql for the admin table: identity ID, name, case-insensitive unique email, super_admin role, active status, and creation timestamp.
- Created the local mikan PostgreSQL database and applied backend/schema.sql successfully.
- Verification passed: pip check, psycopg connection, super_admin/active defaults, case-insensitive email uniqueness, and rejection of non-super_admin roles in admin.
- All verification inserts were rolled back; the admin table is empty.
- No admin account has been seeded. Email OTP login is planned; no password column was added.

## Frontend Login
- Initialized Next.js 16.3.8 with React 19.2.8, TypeScript, App Router, and ESLint in frontend, without creating a Git repository.
- Installed lucide-react and locally bundled DM Sans / Instrument Serif fonts. npm's package-lock.json records exact dependency versions; retain and update it on installation.
- Created the login page with the reference's split visual/form layout, Mikan-specific wording, the supplied logo at the top, and a Powered by Zyora Labs footer linking to https://zyoralabs.com.
- Logo source: https://www.mikan-groups.com/img/flomatexs.jpg (stored locally). Architectural photograph: https://images.unsplash.com/photo-1486406146926-c627a92ad1ab (stored locally).
- Sign-in is UI-only: native email validation and an explicit not-connected notice. No OTP is sent and no successful authentication is simulated.
- Verification passed: TypeScript check, Next.js development compilation, and editor diagnostics.
- Browser checks passed: desktop/mobile screenshots, loaded logo and photo, email validation, honest not-connected status and reset, correct Zyora Labs link, and no horizontal overflow at widths 320, 390, 768, 1024, 1440, and 1920.
- Development preview: http://127.0.0.1:3000, running through the Mikan frontend dev task in .vscode/tasks.json.
- Production build and final lint are deferred until visual approval, following the requested UI workflow. Authentication remains unconnected.
- npm reports five high-severity advisories in the development-only ESLint dependency chain (braces, micromatch, fast-glob, Next ESLint plugin/config). The suggested automated fix downgrades the Next ESLint config; no forced downgrade applied.

## Hydration Compatibility
- Added suppressHydrationWarning only to the root body to tolerate extension-injected attributes such as cz-shortcut-listen. Descendant hydration checks remain enabled.
- Editor diagnostics passed. Automated pre-hydration attribute injection could not be verified because intercepted browser reloads timed out; confirmation in the affected browser remains pending.

## Version Control
- Initialized Git on main for the full workspace; remote target: https://github.com/Zyora-Dev/mikan.git.
- Added root ignore rules for Python environments, secrets, local databases, caches, and generated files. Verified exclusions for .venv, backend/.env, node_modules, .next, and .DS_Store.
- Remote had no existing refs before the initial commit. Initial snapshot includes backend setup, database schema, frontend login, assets, quotation, and progress tracking.

## Discussed Scope (Not Implemented)
- Company management, user/team management, and settings including storage configuration.
- Support Cloudflare R2, AWS S3, and DigitalOcean Spaces via S3-compatible storage integrations, with provider-specific settings and capabilities.
- No backend application routes, authentication flow, or storage integrations have been created. Frontend login UI exists; company/user/settings modules remain unimplemented.