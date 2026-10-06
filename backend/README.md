# Mikan Administration

Team sign-in remains at `/`. Super admin email/password sign-in is at
`/admin/login`, with a server-verified account page at `/admin`.

## WhatsApp

Super Admin > WhatsApp (`/admin/whatsapp`) contains Settings, Consents and
Send Template. Save the Meta phone number ID, business ID and access token.
Tokens use the existing integration encryption key and are never returned by
the API; leaving the replacement token blank retains the existing one.

Record the company name, international recipient number (including `+` and
country code), consent source/evidence and explicit opt-in. This is an admin
record of consent already obtained, not a public opt-in collection page.
Revocation keeps the record but prevents future sends. Search, UTC date filters
and pagination apply to consent records and the active-recipient dropdown.

Sending requires selecting an active consent, entering the company-name value
and confirming the recipient. The backend sends `onboarding_client`, language
`en`, with one positional body text parameter through Meta Graph API v25.0.
The template must already be approved in the sender's WhatsApp Business Account.
The business ID is retained in settings; sending uses the phone number ID.
The access token must have the required WhatsApp messaging permissions.

Routes under `/integrations/whatsapp` require a Super Admin session. GET/PUT on
the root read/save settings; GET/POST `/consents` list/record consent;
POST `/consents/{id}/revoke` revokes it; POST `/send` accepts `consent_id` and
`company_name`. Mutations require the trusted origin. Provider errors and tokens
are not returned. A recipient lock and 60-second cooldown prevent rapid repeat
sends. There are no automatic send retries. Last attempt/outcome/message ID are
retained on the consent record. `accepted` does not mean delivered; delivery
webhooks are not included. After an `unknown` outcome or interrupted request,
check WhatsApp before retrying: exactly-once delivery is not guaranteed.

Migration024 adds only the two WhatsApp tables and their indexes. Existing
company cleanup does not remove these independent consent records/settings.
Local tests use mocked Meta responses; no real messages were sent.

## Super Admin Management

The Companies and Admins screens provide create, list, edit and delete controls.
Admin edits revoke existing sessions; platform super-admin accounts cannot be
edited or removed through company-admin routes. Members are selected by company
and reuse team invitations, activation, editing and access revocation. Permanent
member/company deletion rejects dependent records and directs the administrator
to Clear Data where necessary.

Clear Data starts with no company or category selected. Choose one company or
explicitly all companies, then select companies, company admins, members, files
and/or folders. `POST /admin/management/clear/preview` returns counts, dependency
blockers and a fingerprint. Confirming requires the exact text `CLEAR DATA`.
Each `POST /admin/management/clear/execute` cleans at most one file and its
revisions; repeat with the same confirmed payload until `done` is true.
Changed previews require a fresh review, including a check under file locks
before provider deletion. All routes require a super-admin session and mutations
require the trusted origin.

Storage cleanup must be confirmed before quota release or final metadata removal.
Only the final metadata step is transactional: previously removed objects cannot
be restored after a partial failure or timeout. Failed purging objects may be
retried by the background worker. The UI reports this and requires a fresh
preview after failure. Related workflow runs/jobs/history are removed with files;
member removal also removes workflow definitions and authentication records;
company removal also removes teams, policies and company history. Dependency
categories are never selected automatically. Platform super-admin accounts and
integration settings are preserved. No schema migration is required.

## Company Storage Dashboard

`GET /company/teams/insights/storage` returns company-session-scoped totals
(`used_bytes`, `capacity_bytes`, `remaining_bytes`, `teams`) and five ranked rows
per team, member and largest-file preview. Company capacity is the confirmed
16 TB (16,000,000,000,000 bytes), independent of team allocations. Used bytes use authoritative team
counters; member usage includes canonical reservations and charged revisions,
including original-team usage after reassignment. Remaining bytes equal company
capacity minus used bytes, clamped at zero. Team quotas are unchanged.

The original Company Details, Members and Storage cards remain above the new
storage section. The company dashboard uses the private/no-store `/api/company/insights/storage`
proxy and links to full reports. Storage reports support `sort=usage` and remain
current snapshots, rejecting historical date filters. The new `largest-files`
report supports creation-date filters, search, pagination and CSV export. It
lists canonical file sizes with owner, team and state, excluding cancelled and
purged files; retained revisions affect usage totals rather than appearing as
separate largest-file rows. Dashboard and report sizes use decimal units with
exactly two decimal places. Manual refresh reloads the dashboard summary.

## Member Storage Summary

`GET /team/storage` returns `used_bytes`, `quota_bytes` and `team_used_bytes`
for the authenticated member's current team. Personal usage includes owned
canonical file reservations until cancelled/purged, plus retained/pending revision
`quota_bytes`; current-version aliases are not double-counted. Shared recipient
files and files in other teams are not charged to this personal total.

The private/no-store `/api/team/storage` proxy accepts GET only. The team sidebar
shows personal usage and one remaining-team-space line (allocation minus all team
usage), using decimal MB/GB/TB displayed to exactly two decimal places.
Includes loading, retry and no-allocation states. It refreshes on pathname
navigation and background upload completion/failure/cancellation; manual refresh
covers changes elsewhere, including dialog-local version operations.

## Local Setup

Run commands from the repository root using the existing `.venv`.
The local database is `mikan`. The initial `backend/schema.sql` has already
been applied: do not rerun it against an existing database.

For an existing database on another installation, apply the additive migration:

```sh
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/001_admin_auth.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/002_companies.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/003_zeptomail.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/004_company_admins.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/005_teams.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/006_storage.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/007_private_files.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/008_team_folders.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/009_team_folder_activity.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/010_workflows.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/011_automation.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/012_data_administration.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/013_notifications.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/014_notification_events.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/015_multipart_uploads.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/016_file_sharing.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/017_share_links.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/018_file_permissions.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/019_file_versions.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/020_upload_fingerprints.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/021_upload_cancellation.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/022_trash_retention.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/023_team_profile.sql
psql -d mikan -v ON_ERROR_STOP=1 -f backend/migrations/024_whatsapp.sql
```

All twenty-four migrations are already applied on this development machine. They preserve
existing accounts; an account without a password hash cannot use password login.

Start the backend with the **Mikan backend dev** VS Code task, or:

```sh
.venv/bin/python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

The existing frontend development task serves http://127.0.0.1:3000.

## Notifications And Storage Alerts

Header bells open `/admin/notifications`, `/company/admin/notifications`, or
`/team/notifications` for the signed-in role. All/Unread/Read tabs are URL-backed;
search, inclusive UTC dates, category (company and team), and ten-row pagination filter
the current view. Each recipient has independent read/unread state. Team workflow
notifications reuse existing records, so marking read in either workflow history
or the notification center updates both. Request links retain existing request
authorization. Bells and open inboxes share debounced refresh events from an
authenticated Server-Sent Events stream. Counts also refresh on navigation and
focus; a one-minute polling fallback runs when streaming is unavailable.

- Submitters receive submission receipts, individual approval decisions and final
	results. Existing assigned-review and configured in-app workflow notices remain.
	Cancellation notifies the submitter and configured reviewers. Active company
	admins receive submission, decision, cancellation and completion events from
	their own company; super admins do not receive tenant workflow events.
- Exhausted automation retries notify active company admins and the file owner
	if still active in the file's company/team. Alerts deduplicate per job/recipient,
	including repeated exhaustion after manual retry. Transient failures remain in
	the automation log. Alerts contain no provider error details.
- Migration014 preserves existing notices/read state and does not backfill
	historical events. File sharing uses the separate email queue described below;
	sharing does not add notices to this in-app notification center.
- PostgreSQL change signals are delivered after commit and contain recipient IDs
	only. One visible header stream uses one dedicated database listener connection;
	hidden tabs close it. Reconnection refreshes the inbox to recover missed updates.
	Sessions and account identity are rechecked on events/15-second heartbeats;
	expired or reassigned sessions close. Streams rotate around two minutes.
	This is in-app delivery, not operating-system push; closed tabs receive no live
	alert. Authenticated browser end-to-end behavior and connection capacity have
	not been load-tested.

Company admins configure Storage alerts in each Team Folder's settings dialog.
Defaults are warning at 80%, critical at 90%, and full at 100%. Warning and critical
must be whole percentages with `1 <= warning < critical <= 99`; full is fixed.
Legacy quota/access saves preserve alert settings. Settings changes are recorded
in the company's data activity/audit history.

- Alerts use registered **used and reserved** bytes, including pending,
	quarantined and trashed metadata, across the one company-facing storage service.
	Zero allocation does not alert. No provider objects are read or changed.
- An atomic database trigger sends only the highest newly crossed band to active
	super admins, active admins of that company and active accounts in the affected
	team. Same-band updates do not repeat; dropping below a band and crossing it
	again sends a fresh notice. Quota and alert-setting changes are evaluated too.
	Re-enabling alerts above a threshold sends the current band.
- Migration does not backfill existing usage or historical alerts. New accounts
	do not inherit previously delivered alerts. Team storage notices from an old
	team are hidden after reassignment; read mutations enforce the same boundary.
	Delivery is in-app only, with no email or push delivery.
- Backend prefixes are `/admin/notifications`, `/company/notifications` and
	`/team/notifications`. Each supports GET root, `/unread` and `/stream`, POST `/read-all`
	with an aware `before` timestamp from the inbox response, and POST
	`/{storage:id|workflow:id}/read` with a strict boolean `read`.
	Mark-all-read covers the recipient's whole inbox through that snapshot,
	regardless of active filters, and leaves later arrivals unread.
- Role-specific Next proxies forward only the correct session cookie, allowlist
	routes/query parameters, enforce trusted mutation origins and JSON bodies up
	to 1024 bytes, suppress upstream redirects and unsafe headers, and return private
	non-cacheable responses. There is no delete-notification API.

## Audit Logs And Reports

Company Admin pages are `/company/admin/audit` and `/company/admin/reports`;
Super Admin pages are `/admin/audit` and `/admin/reports`. Source and report
tabs persist in the URL. Other filters are local to the current page.

Read-only backend routes use `/company/teams/insights` for the authenticated
company and `/admin/insights` for Super Admin. Both expose `/audit` and
`/reports/{report}`, where report is `storage-teams`, `storage-employees`,
`activity`, `workflows`, or `automation`. The company route rejects attempts
to select another company. Super Admin can optionally filter `company_id`.
Next.js proxies at `/api/company/insights` and `/api/admin/insights` forward
only the corresponding role cookie; team sessions grant no reporting access.

- Audit history combines existing data-administration, team-folder and
	workflow events with source-prefixed IDs. This is not a complete security,
	authentication or configuration audit trail, and it is not tamper-proof.
	No historical actions are reconstructed. Unknown creators remain unknown;
	`tracking_started` is a snapshot, not an earlier settings-change history.
	Downloads record requests, not proof of completed delivery. Owner uploads
	and owner downloads without recorded events are not synthesized as activity.
- Storage reports are current reserved usage, including pending, quarantined
	and trashed metadata; they reject date filters. Employee usage is grouped
	by employee and original drive team, including old drives after reassignment.
	Employee quotas are not invented; allocation is team-level. Byte units in
	the UI are decimal; CSV values remain exact byte counts.
- Activity filters event timestamps. Workflow reports filter request creation
	dates and show current request states and cumulative task approval/pending
	counts, not decisions made during the selected period. Automation reports
	filter job creation dates and show current outcomes/attempts. Failed jobs
	are separate from request statuses; payloads and internal errors are omitted.
- `search`, `from_date`, `to_date` and `page` select matching rows, ten per page.
	Dates use inclusive UTC calendar days. Audit also accepts `source` and exact
	`action`. Searches are literal, not wildcard expressions. The UI shows an
	as-of timestamp; reports are live queries, not historical snapshots.
- `export=true` exports all matching rows, ignoring page, as UTF-8 CSV with a
	BOM. Exports over 5,000 rows return413 instead of truncating: narrow the date
	range/search/company filter. Spreadsheet formula-like strings are escaped.
	All insights responses, including authentication errors, are private/no-store.

No additional migration is required after migration012. Focused regression
tests are `test_insights_scopes_reports_and_csv`,
`test_insights_date_pagination_and_storage_accounting`, and
`test_insights_workflow_outcomes` in `test_auth.AdminAuthTests`.

## Private File Access

The backend `GET /team/files/{uuid}/content` and same-origin browser
`GET /api/team/files/{uuid}/content` routes serve registered files without
redirecting to R2 or S3. They require the separate team session cookie.

- Default policy: only the active owner in the recorded company and team can
	download a ready file from an enabled connection. Company admins can enable
	read-only access for that team's current manager in Team Folders. The setting
	is checked on every download; disabling it removes access on the next request.
	Ordinary teammates, company admins and super admins have no implicit access.
- An explicitly assigned reviewer can download only the unchanged submitted file
	while their current approval task is pending and the workflow is enabled.
	This grant does not expose the owner's drive or other company files. Workflow
	completion, cancellation, pause, account/team changes or file changes revoke
	the grant; independent owner/manager permissions still apply.
- File IDs are opaque UUIDs, not authorization. Ownership comes from database
	records and the current session; caller-supplied tenant IDs or object keys
	never determine access. Missing and inaccessible files both return404.
- Metadata defaults to pending. Pending, quarantined and trashed files are
	unavailable. Expired/revoked sessions and disabled accounts are rejected on
	the next request. Changing teams removes owner/manager access to the old
	team's records; explicit company-wide recipient grants are separate.
- Downloads validate the recorded ETag and size and use the recorded AWS object
	version when present. R2 requests omit VersionId. Provider failures are redacted.
- All responses disable browser/CDN caching. Downloads force attachment and
	application/octet-stream with nosniff and sandbox headers; uploaded HTML is
	not rendered on the application origin. Proxy responses stream bytes instead
	of buffering the whole file and forward only the team session cookie.
- Registered storage connections cannot be removed or redirected through the
	settings API. Disabling, renaming and credential rotation remain available.
	Future upload/registration code must lock the connection while binding files.

Private previews use `GET /team/files/{uuid}/preview` through the same-origin
`/team/files/{uuid}/view/media/{encoded filename}` frontend route (the original
`/api/team/files/{uuid}/preview` proxy remains available), with the same session and file
authorization as downloads. Successful previews use inline disposition, a fixed
filename-extension MIME allowlist, nosniff, no-store and same-origin-only framing.
Supported formats are JPEG, PNG, GIF, WebP, AVIF, BMP, MP4/M4V, WebM, MOV and PDF.
HTML, SVG, HEIC, MKV and office documents remain download-only. There is no format
conversion, content-signature validation or video transcoding; browser codec
support determines playback. Preview support is not a malware scan.

Single byte ranges (including open-ended and suffix ranges) return206 with
Content-Range and Accept-Ranges for video/PDF seeking. Invalid or unsatisfiable
ranges return416. Provider responses must match the requested length, range and
recorded ETag; AWS version pinning is retained. The proxy streams identity-encoded
responses only. Existing attachment downloads are unchanged.

My Files opens supported ready files in a native dialog with image zoom/fit,
video controls or the browser's PDF viewer, previous/next supported files on the
current page, download and new-tab actions. Closing removes the media elements.
New tabs open the protected frontend `/team/files/{uuid}/view` page with the
stored filename in the header and browser title; closing returns to My Files.
The page reads the authoritative filename from an authenticated one-byte range
response, then cancels its body. Named media URLs preserve private range streaming;
their filename segment is cosmetic and does not grant access or select a file.
PDF controls and mobile embedding depend on the browser; new-tab/download actions
remain available. Grid images use lazy-loaded originals up to20MiB, not generated
thumbnails; larger images retain an icon but can still open in the viewer. Grid
videos request metadata and a first frame; browsers/providers may fetch additional
bytes to decode it. No files are sent to external office-viewer services.

### Company File Sharing

Migration016 adds company-bound recipient grants without copying objects or
charging quota again. Ready-file owners can use the three-dot Share action and
search active company members by name or email across all teams. Suggestions
include email and team names; disabled/invited accounts, the owner and existing
recipients are excluded. Suggestions load only after entering a nonblank name or
email; selections persist across searches. External recipients cannot be invited.
Migration017 adds one stable opaque URL per file, restricted by default, and a
durable email queue for new grants. Historical grants are not emailed.

The Share dialog shows General access, File URL and Copy link above member search.
Restricted links require a signed-in owner or explicitly granted active member
of the same company. Anyone with the link permits anonymous preview/download.
Switching back to Restricted keeps the same URL and blocks subsequent anonymous
requests. Public links can be forwarded by anyone; removing a named grant does
not block that person's anonymous access while public access remains enabled.
Already downloaded or in-flight bytes cannot be recalled. Buckets remain private.

The canonical `/share/{token}` page previews supported images, video and PDFs;
other types and empty files offer download. Restricted anonymous visitors sign in
and return to the same file. Return targets accept only exact shared-file paths.
Metadata, downloads and preview streams are permission-checked on every request,
with no-store, no-referrer and noindex headers. Page responses also disable CDN
caching; Next's development server overrides HTML Cache-Control with
`no-cache, must-revalidate`. Production page headers require deployment verification.

- `GET /team/files/{uuid}/share-members?search=...` returns up to10 suggestions.
- `GET /team/files/{uuid}/shares?page=1` lists existing grants in ten-item pages.
- `POST /team/files/{uuid}/shares` accepts `{"recipient_ids":[123,456],"permission":"view"}` with
	up to50 IDs. All recipients must be active and in the owner's company; invalid
	batches are rejected atomically and duplicate grants are idempotent. Permission
	defaults to `view`; `edit` requires restricted link access.
- `POST /team/files/{uuid}/shares/{recipient_id}/permission` sets `view` or `edit`.
- `POST /team/files/{uuid}/shares/{recipient_id}/remove` removes a grant.
- `GET /team/files/{uuid}/link` returns the owner's stable URL and `public_access`.
- `POST /team/files/{uuid}/link` accepts a strict boolean `public_access`.
- `GET /team/files/link/{token}` returns authorized file metadata; `/content`
	and `/preview` reuse protected streaming. Restricted link access does not
	implicitly include manager or workflow permissions.
- `POST /team/files/{uuid}/shares/{recipient_id}/retry-email` requeues failed or
	cancelled notifications. The grants list includes `email_status`.
- `GET /team/files/shared` lists the authenticated recipient's available shared
	files with ten-item pagination, filename search and UTC created-date filters.

New grants queue text email containing sender name/email, filename, the current
View or Edit permission and the same Open file URL. The existing enabled Zoho CPaaS integration
and trusted `TEAM_PUBLIC_ORIGIN` supply delivery and link configuration. The
lifespan worker processes up to10 messages per cycle when
`MIKAN_AUTOMATION_ENABLED=true` (default). It retries provider/configuration
failures up to five attempts with backoff, then exposes an owner retry action.
Duplicate grants do not resend email. Removed grants disappear from the queue;
inactive recipients or unavailable files cancel pending delivery. Accepted means
the provider accepted the request, not confirmed inbox delivery. Delivery is
at-least-once and can duplicate if a worker stops after sending but before commit.
Reopen the dialog to refresh delivery status. Real provider delivery is unverified.

Share management requires current file ownership and approved request origins
for mutations. Shared with me is URL-backed at `/team/files?scope=shared`, with
grid/list/compact layouts, sender names, filters, preview and download. Recipients
cannot reshare; restricted Edit recipients can upload revisions as described below.
Grants are company-wide, so recipients retain
them when changing teams within the same company. The file owner must remain
active in the file's original team; file-ready and enabled-storage checks apply
on every listing/preview/download request. Removing a grant prevents subsequent
requests through that grant; independent manager/workflow permissions still
apply, and bytes already downloaded or streamed cannot be recalled.

### Restricted Edit And Version History

Migrations018/019 default existing grants to View and backfill version1 for existing
ready, quarantined and trashed files. View permits preview/download; Edit also
permits uploading a revised file with the same filename. This is not an in-browser
document editor. Edit is available only while the file is restricted. Switching
to public access changes all Edit grants to View and blocks revision uploads and
restores, including the owner's. Switching back does not restore Edit grants.

The Share dialog has a permission selector for new recipients and each existing
recipient. Ready files have a Version history action in My Files and Shared with
me. Authenticated restricted shared links also expose history. The dialog provides
upload progress/retry, historical downloads, owner-only restore with confirmation,
and date-filtered, ten-item version and activity pages. Public visitors cannot
access history or activity, even with the file UUID.

- `GET /team/files/{uuid}/versions` returns history, current version and capabilities.
- `POST /team/files/{uuid}/versions` reserves a revision using `name`, `size_bytes`,
	UUID `upload_key` and integer `base_version`. Existing upload/multipart endpoints
	accept the returned revision UUID; credentials and provider keys remain private.
- `GET /team/files/{uuid}/versions/{version_uuid}/content` downloads a ready version.
- `POST /team/files/{uuid}/versions/{version_uuid}/restore` creates a new numbered
	current entry from an existing object; only the restricted file owner may restore.
- `GET /team/files/{uuid}/activity` records uploads, restores, grants, permission
	changes, removals and public/restricted changes from this implementation onward.
	It does not backfill historical actions or log every preview/download.

The canonical file UUID, filename and share URL stay stable. Pending revisions do
not replace the current object until provider metadata is verified. Every upload
stage rechecks access; stale base versions return409 instead of overwriting newer
content. Owner/recipient activity and enabled-storage checks also apply to history.
Revisions do not enqueue the existing new-file automation trigger.

Current, retained and pending revision bytes count against the original owner's
team quota, even when another team's member uploads. Restoring uses existing
immutable objects and does not duplicate their storage charge. There is no version
purge, retention limit or abandoned-reservation cancellation yet. Failed/stale
pending versions retain their quota; clearing the file selection does not release
it. Do not delete metadata to reclaim quota without verified provider cleanup.
Provider lifecycle settings must preserve referenced historical objects.

Revision transfers run in the dialog, separately from the multi-file queue. Keep
the page open; dialog close/Escape are blocked during transfer and tab unload warns.
Browser history/navigation is not a persistent background-upload guarantee. After
interruption, reselect the original pending revision file with the same name/size;
confirmed multipart parts can resume while its base version is still current.
Real-provider large-file and authenticated visual checks remain pending.

Migration007 creates metadata only; it does not register or move existing bucket
objects. There is no user-facing registration endpoint accepting arbitrary keys.
Owner upload/list/download is available at `/team/files`, including logical folder
paths. `GET /team/files` without a `folder` query keeps the flat file listing used
by workflow forms. `GET /team/files?folder=` browses the drive root; a nonempty
`folder` selects that exact relative path. Browse results contain immediate
children with `kind=file|folder`, including explicit empty folders and ancestors
derived from file paths. Folder entries include `path`; their size/state are null.
Both admin and member APIs use the same database folder resolver. Browse results
exclude trashed files, remain scoped to the session company/team/owner, and use
ten-item pages ordered by folders first, name and ID. Search/date filters apply
to immediate children. Invalid paths return422; absent paths return404.
The member UI preserves folder/layout in the URL, provides breadcrumbs and
defaults uploads to the open folder. Refresh retrieves externally changed paths.
Members can create a logical folder with `POST /team/files/folders` and JSON
`{"name":"Reports","parent":""}`; parent is the existing relative folder path
or empty for the drive root. Company, team and owner come only from the session.
Creation uses the existing data_folder table and records data_activity; it does
not require uploads to be enabled. Invalid names/paths return422, missing parents
return404 and existing folders return409. Member rename/removal is not exposed.
My Files provides compact search/date controls, a New folder dialog and grid
cards with file-type artwork, title, creation time and three-dot actions.
Absent or disabled company upload policy still blocks uploads; a company admin
must enable them in Workflows > Upload settings. The member control explains
this restriction without a separate persistent banner.
No database migration or cloud object movement is required for this connection.
Managers can browse active own-team member drives at `/team/drives?owner={id}`
when the company admin enables manager viewing in Team Folders. This read-only
route supports folders, search/date filters, pagination, layouts, preview and
download. It does not expose upload, sharing, version changes or deletion.
Arbitrary bucket imports are not implemented. No existing cloud files were modified.

Keep both buckets private. This local application boundary does not audit or
enforce cloud bucket policies and cannot protect a separately public bucket URL.
Deployment must bypass CDN caching for private routes. The planned separate
storage domain is not deployed; its authentication must be designed explicitly
without widening the current host-only session cookies. Downloads already sent
cannot be recalled, and access revocation does not retract an in-flight download.

Focused backend verification (mocked providers, rollback-isolated database data):

```sh
.venv/bin/python -m unittest discover -s backend -p test_auth.py -k private_files
```

## Workflows and Automation

Company admins author workflows at `/company/admin/workflows`. The React Flow
canvas supports draggable trigger, approval, notification, file-action and end steps,
outcome connections, graph undo/redo, a settings inspector, draft saves,
validation and publishing. Published graphs must be connected and acyclic, with
exactly one manual, upload or interval trigger and every outcome connected.

Admins configure eligible teams and submitting roles, fixed or submitter-tagged
reviewers, company/team/selected-person scope, self-review policy, all/any/sequential approval,
notification recipients and terminal results. Only company admins author
definitions; delegated workflow authors are not implemented.

Team accounts use `/team/workflows` for eligible workflows, their requests,
assigned reviews and notifications. Submission selects an existing ready file
owned by the signed-in account and tags eligible reviewers for each tagged step.
Request detail pages show review tasks and activity, permitted private downloads,
approval/rejection/change requests and cancellation. Rejection and change requests
require a reason. Lists provide search, UTC date filters and pagination.

- Backend admin APIs are under `/company/teams/workflows`; team APIs are under
	`/team/workflows`. Browser proxies use `/api/company/teams/workflows` and
	`/api/team/workflows`, with exact path/method allowlists and separate cookies.
- Each request retains its workflow version, graph and file snapshot. Editing a
	definition does not rewrite existing runs. Pausing blocks decisions and reviewer
	file grants; resuming restores eligibility for otherwise valid pending tasks.
- Submissions serialize against definition edits and duplicate request keys.
	The UI sends its displayed version so stale forms return409. Definitions also
	use optimistic version checks. Concurrent race behavior has not been load-tested.
- Approval branches execute transactionally. All-reviewer steps wait for every
	approval; any-reviewer steps advance on the first approval. Rejection or a change
	request immediately follows its configured branch and cancels remaining tasks.
- Notifications support in-app, email and configured signed HTTPS webhooks; none
	grant file or request access. History has
	no edit/delete API, but is not a tamper-proof ledger or a log of direct DB edits.
- Automatic workflows require fixed reviewers. Only manual workflows appear in
	the team submission picker. Upload triggers apply after provider confirmation,
	optionally matching a case-insensitive filename suffix and exact logical folder.
- File actions rename a file or change its logical folder inside its owner's drive.
	They never transfer ownership, grant access, copy objects, or change storage keys.
	Run history retains the original submitted filename.

Focused tests use rollback-isolated records and mocked providers:

```sh
.venv/bin/python -m unittest discover -s backend -p test_auth.py -k workflow
```

Automation tests use `-k automation` with the same command. All41 backend tests,
frontend TypeScript/scoped ESLint,18 mocked proxy checks and10 signed-out local
HTTP checks pass. Authenticated visual verification, real-provider automation
checks, concurrency/crash testing and the production build remain pending;
nothing has been deployed.

### Uploads and Recovery

Configure a positive team allocation in Team Folders, then enable uploads in
Workflows > Upload settings. Companies see one storage service: no provider names,
connection choices or connection IDs are exposed or accepted by this settings API.
Provider configuration belongs to the platform super admin. New policies internally
use the first enabled connection by ID; existing assignments remain unchanged on
settings saves, including when disabled. Unavailable storage produces a generic
message to contact the platform administrator, not a provider choice. Uploads are
disabled until configured. One shared contract applies to all storage: single-request
uploads accept up to 5 GB (5,000,000,000 bytes); multipart files accept up to 4 TiB
(4,398,046,511,104 bytes). New policies default to 4 TiB; existing saved limits and
enablement remain unchanged. The company can set a lower per-file limit in Upload
settings using Bytes, MiB, GiB or TiB. Team quota still applies to the entire file.

`POST /team/files/uploads` reserves quota for an owner-scoped UUID/request key.
`POST /team/files/{uuid}/upload` accepts `application/octet-stream`, streams to a
bounded temporary file and confirms the provider ETag/size before marking ready.
`POST /team/files/{uuid}/multipart` starts or resumes a persisted multipart session;
`POST /team/files/{uuid}/parts/{number}` accepts one exact-sized binary part;
`POST /team/files/{uuid}/multipart/complete` verifies the provider's complete part
manifest, completes it, then confirms object size, ETag and reservation metadata.
Only then does the file become ready and trigger automation. Provider upload IDs,
object keys and part ETags never leave the backend. R2 omits VersionId; AWS records
it when returned. All operations recheck current owner, team, policy and storage.

The browser automatically uses multipart above 16 MiB; smaller and empty files use
a single request. Parts are at least 16 MiB, rounded up to whole MiB to keep the
file within 10,000 parts (at most 420 MiB each at the 4 TiB limit). Parts stream via
the private proxy to bounded temporary files. Progress includes already-confirmed
parts; transient transfer failures retry at most three times per attempt. The
five-minute timeout is per request, not per whole multipart file. No upload-mode
or provider selection is shown. Production proxy limits/timeouts and temporary
disk capacity must accommodate these requests; the direct single-upload endpoint
can require 5 GB of temporary disk per concurrent request.

Retries reuse the same reservation/object key. Failed or interrupted uploads
retain their quota reservation; My Files offers retry using a file with the same
name and size. Multipart retries list and skip confirmed parts; an expired session
restarts if no completed object exists. Lost completion responses recover only
after verifying object size and reservation metadata. Same name/size does not prove
content identity: always reselect the original file. Pending files are not
downloadable. There is no abandon/purge API:
do not remove metadata to recover quota without first verifying provider cleanup.
Changing upload policy/storage can block old reservations until configuration is
restored. Versioned AWS buckets may retain older retry versions; those versions
are not included in recorded quota. Configure provider lifecycle rules to abort
abandoned multipart sessions before deployment; a provider session created just
before a database failure can also be orphaned. Cleanup does not automatically
release the application's pending quota reservation.

Local checks use mocked provider responses, including the 4 TiB boundary without
allocating a multi-terabyte file. Actual provider end-to-end, large transfer/load
tests, browser visual review and the production build remain pending.

### Worker and Delivery

The backend lifespan runs the worker by default; `MIKAN_AUTOMATION_ENABLED=false`
disables it without deleting jobs. Each cycle schedules up to 100 eligible files
per workflow and executes up to ten due jobs, then waits 15 seconds. Jobs persist
in PostgreSQL and workers claim them with row locks and `SKIP LOCKED`.

Intervals are 5 to 525600 minutes, aligned to UTC epoch slots. Enabling a schedule
can process existing eligible files immediately in the current slot. A file is
queued once per workflow version/slot; missed slots during downtime are not
replayed. A pending run prevents another run for that workflow/file. Queued
triggers are cancelled if the definition version changes or the owner is no
longer eligible. Paused workflows block execution; pending deliveries recheck
the file snapshot, owner, storage and recipient before sending.

External notification steps wait for all delivery jobs before advancing. Failures
retry with exponential backoff, stop after five attempts, and remain visible in
Workflows > Automation log. The company admin can explicitly retry failed/retry
jobs. Email uses the enabled platform Zoho CPaaS integration and active company
recipients. Provider acceptance does not guarantee inbox delivery.

External delivery is **at-least-once**, not exactly-once: a provider can accept a
message before a crash, timeout or downstream transaction failure. A retry may
send it again. Webhooks supply a stable `Idempotency-Key`; receivers must deduplicate.
Emails can duplicate. Delivery jobs, workflow history and reservations currently
have no retention policy. Concurrency/load and crash recovery need deployment testing.

### Webhook Configuration

The server administrator configures company-specific destinations through the
secret environment variable `MIKAN_WEBHOOKS`; the canvas exposes only their names:

```json
{"123":{"audit":{"url":"https://receiver.example/events","secret":"replace-with-a-random-secret-at-least-32-characters"}}}
```

Replace `123` with the company ID. Store configuration only in the protected
runtime environment, never workflow JSON or source control. Destinations must
use HTTPS port443, no URL credentials/fragments, and resolve exclusively to
public IP addresses. Connections pin a resolved IP while verifying the original
TLS hostname; redirects are rejected. Restrict network egress as defense in depth.

Payload fields are `event_id`, `run_id`, `workflow`, `file_id` and configured
`message`, not file contents, object keys or download links. Verify
`X-Mikan-Signature: sha256=<hex>` using HMAC-SHA256 over the exact request body,
the configured secret and constant-time comparison. Return a 2xx response on
acceptance, including previously accepted idempotency keys. Keep deduplication
records long enough for delayed/manual retries. DNS resolution relies on the
host resolver; connect/read timeouts do not impose a separate DNS deadline.

## Data Administration

Company admins manage their company's registered employee files at
`/company/admin/data`. Apply migration012 before using this module.
The matching proxy is `/api/company/teams/data`; backend routes are:

| Method | Backend path | Purpose |
| --- | --- | --- |
| GET | `/company/teams/data/files` | Files or trash; search, UTC dates, team/owner/folder filters and 10-row pagination |
| GET | `/company/teams/data/folders` | Explicit empty folders plus inferred ancestor paths; search, UTC dates and pagination |
| POST | `/company/teams/data/folders` | Create, rename a subtree, or remove an empty folder within one employee drive |
| POST | `/company/teams/data/files/{uuid}` | Rename/move, trash, or restore a file, rejecting stale name/folder/state |
| GET | `/company/teams/data/files/{uuid}/content` | Authenticated attachment stream with private no-store headers |
| GET | `/company/teams/data/activity` | Company-admin operations and download requests; search, UTC dates and pagination |

Company identity comes only from the company-admin session. Team sessions and
platform-super-admin sessions do not grant access here. Provider connections,
object keys and credentials are never exposed in management listings.
Ownership, physical keys and quota charges are unchanged by these operations.
Trash blocks downloads and pending workflow access; restoring may make an
unchanged workflow snapshot accessible again. Only files trashed by this module
can be restored here. Pending/quarantined files cannot be changed. Empty-folder
removal rejects files in every state, including trash, and nested folders.
There is no permanent deletion, cross-drive transfer or archive/bulk download.

Folder paths are logical metadata. Upload/workflow paths remain discoverable
without pre-created folders. Inferred folder dates use the earliest contributing
record, not filesystem creation times. Folder mutations currently use transaction
table locks to serialize metadata writes against uploads and workflow moves;
large installations should assess lock contention before deployment.
Activity records admin actions only, not historic/team/workflow operations, and
a download-request event does not prove the complete transfer reached the browser.

## Team Folders

Company admins configure their teams at `/company/admin/team-folders`.
Each existing team has one logical allocation, initially zero, with separate
private member drives. This does not create a bucket or a physical provider folder.

| Method | Backend path | Purpose |
| --- | --- | --- |
| GET | `/company/teams/folders` | Company-scoped teams, usage and settings; search, UTC team creation-date filters and 10-row pagination |
| POST | `/company/teams/folders/{team_id}` | Set `storage_quota_bytes` and `manager_can_view_drives` |
| GET | `/company/teams/folders/{team_id}/activity` | Creation/settings history; UTC `from_date`, `to_date` and 10-row `page` pagination |

The browser uses the matching `/api/company/teams/folders` proxy paths. Only an
active company-admin session can read or change these settings; writes also
require an approved Origin. Team members, managers and super-admin sessions
cannot configure them. Foreign-company team IDs return404.

The history action opens `/company/admin/team-folders/{teamId}/activity`, a dedicated
company-admin page with a Team Folders return link, date filters and pagination.
It shows the folder's original team creation timestamp, allocation
changes and manager-access changes, with UTC timestamps and admin-name snapshots.
Migration009 preserves existing creation dates without guessing creators and adds
an explicit tracking-start snapshot. Earlier settings changes are unavailable.
New team creation and changed settings append activity in the same transaction;
unchanged or rejected saves do not create events. History is company-admin-only,
with no edit/delete API or automatic expiry. It does not log file operations or
direct database edits and is not a tamper-proof audit ledger. Allocation values are
stored in exact bytes; the UI shows formatted sizes with exact-byte tooltips.

- Allocation is shared across all members and storage providers, not per member.
	UI units are decimal GB/TB; the API stores whole bytes, from zero to 9,000 TB.
	Zero means no new file registration, including zero-byte files.
- Migration008 adds allocation, recorded usage and default-off manager access
	directly to each team. Existing metadata is counted without modifying files
	or assigning an allocation automatically; set one before registering more files.
- Atomic database quota accounting covers every `stored_file` record, including
	pending reservations, quarantined and trashed files. Inserts, growth and moves
	cannot exceed the receiving team's quota. Removing metadata releases its bytes.
	Reducing an allocation below recorded usage returns409. Both paths lock the
	same team row so concurrent changes cannot over-allocate.
- Uploads reserve expected size before contacting a provider and verify final size.
	Failed reservations remain available for retry. Future purge code must delete
	the provider object before removing metadata; purge is not yet built.
- Enabling manager viewing grants read access to ready files in that manager's
	recorded company/team only. It does not grant write/delete rights or change
	member-to-member isolation. Role changes and permission removal apply on the
	next request. My Files lists only the signed-in owner's files. Managers use
	My Team's member directory to open a separate read-only drive. The optional
	`owner_id` on `GET /team/files` requires an active own-team owner and the
	current manager-viewing flag; members or managers without the flag receive403.

Usage represents registered metadata, not a scan of existing R2/S3 bucket
contents or historical provider versions. Existing objects must be registered
through a future trusted migration workflow before appearing in usage.

## Create a Super Admin

Replace the example email and name with the intended administrator's details:

```sh
.venv/bin/python backend/create_super_admin.py --email admin@company.com --name "Super Admin"
```

Enter and confirm the password in the terminal's hidden prompts. Use 12-128
characters. Passwords are not accepted in command arguments, printed, or stored
as plaintext. Only the randomly salted scrypt hash is stored in `admin.password_hash`.
The script rejects duplicate email addresses without changing existing accounts.
There is no public registration endpoint and no default admin account.

## Configuration

Backend settings come from environment variables or the ignored `backend/.env`:

| Variable | Local default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `dbname=mikan` | PostgreSQL connection string |
| `ADMIN_ORIGINS` | `http://127.0.0.1:3000,http://localhost:3000` | Exact allowed browser origins, comma-separated |
| `COOKIE_SECURE` | `false` | Set to `true` when serving over HTTPS |
| `TEAM_PUBLIC_ORIGIN` | `http://127.0.0.1:3000` | Public frontend origin used in invitation and recovery links; HTTPS required except local development |
| `INTEGRATION_ENCRYPTION_KEY` | Read `backend/.integration-key` | Fernet key for integration credentials; keep separate from the database |
| `MIKAN_AUTOMATION_ENABLED` | `true` | Enable the backend lifespan scheduler/job worker |
| `MIKAN_WEBHOOKS` | `{}` | Secret company-ID to named HTTPS destination mapping described above |

Frontend settings come from environment variables or the ignored `frontend/.env.local`:

| Variable | Local default | Purpose |
| --- | --- | --- |
| `ADMIN_API_URL` | `http://127.0.0.1:8000` | Private backend URL; server-only |
| `ADMIN_PUBLIC_ORIGIN` | Both local port-3000 origins in development only | Exact browser origin, mandatory outside development |

Before deployment, set the exact HTTPS public origin in both frontend and backend,
enable `COOKIE_SECURE=true`, use a least-privilege database account, and keep the
backend private behind the frontend. Add perimeter/IP rate limiting and request-size
limits; the built-in five-attempt, 15-minute limiter is per email, not a substitute
for edge-level abuse protection. Expired sessions are removed during successful
login; schedule retention cleanup for old login-attempt rows in production.

## Session Behavior

- Password hashing: standard-library scrypt, N=131072, r=8, p=1, 16-byte random salt.
- Random session tokens are stored only as SHA-256 hashes in PostgreSQL.
- Session cookie: HttpOnly, SameSite=Strict, 30-day absolute lifetime from sign-in.
- Login rotates the current browser session; logout deletes its database session.
- Every protected request verifies expiry, active status, and the super_admin role.
- Authentication endpoints: POST `/auth/admin/login`, GET `/auth/admin/me`, POST `/auth/admin/logout`.
- Browser requests use the corresponding same-origin `/api/admin/` routes.
- Login/logout require an approved Origin. Failed login messages do not reveal whether an email exists.
- Admin password reset is not implemented. Team account recovery and email OTP use the separate team endpoints below.

## Company Management

Super admins manage companies at `/admin/companies`. Name, mobile, email, and
address are required; website and logo are optional. Website URLs must use HTTP
or HTTPS. Mobile numbers allow 7-15 digits with an optional leading `+`, spaces,
parentheses, and hyphens. The list supports search, inclusive UTC creation-date
filters, and pagination. Create and edit are supported; company deletion is not.

| Method | Backend path | Purpose |
| --- | --- | --- |
| GET | `/companies/` | List: `search`, `page`, `page_size`, `from_date`, `to_date` |
| POST | `/companies/` | Create a company |
| GET | `/companies/{id}` | Read company details |
| PUT | `/companies/{id}` | Update company details |
| GET | `/companies/{id}/logo` | Read the authenticated WebP logo |

All endpoints verify the active super-admin session. Writes require an approved
Origin. The browser uses `/api/admin/companies` and its ID/logo subpaths through
the same-origin Next.js proxy, which enforces a 3 MB request-body limit. Keep the
backend private and enforce equivalent perimeter limits before deployment.

Create/update use JSON containing `name`, `mobile`, `email`, `website`, `address`,
and optional `logo_base64` (raw base64, not a data URL). The file picker performs
this encoding. Pillow verifies PNG, JPEG, or WebP uploads up to 2 MB, 16 million
pixels, and 8192 pixels on either side. Images are re-encoded without source
metadata to WebP, at most 512px on either side. Company fields and logo bytes are
saved together in PostgreSQL; no public upload directory is used. Logo responses
are private, uncached, and served with `nosniff`.

On edit, omit `logo_base64` or send `null` to retain the existing logo; send
`remove_logo: true` to remove it, or a new `logo_base64` to replace it. Removal and
replacement cannot be requested together. An invalid logo prevents the whole save.

## Storage Connections

Super admins configure multiple connections under **Integrations > Storage** at
`/admin/integrations/storage`. Select Cloudflare R2 or AWS S3, then Add connection.
Each connection requires a name, an existing bucket and an access/secret key pair.
Saving does not contact the provider or create a bucket. Enabled is a configuration
flag controlling connection availability. Team Folders manages shared logical
quotas separately; upload routing, nested folders and migration remain pending.

- R2: enter the 32-character Cloudflare account ID and select Default or EU
	jurisdiction to match the bucket. Region is `auto`; the S3 endpoint is derived.
	Use R2 S3 access credentials, not a generic Cloudflare API token. Grant Object
	Read & Write scoped to the intended bucket. Other jurisdictions are not supported yet.
- AWS S3: select the bucket's commercial AWS region. The endpoint is derived;
	custom endpoints, China/GovCloud partitions, STS tokens and role assumption are
	not supported. Use a dedicated least-privilege IAM principal, not root keys.
	The test needs `s3:ListBucket` on the bucket and `s3:PutObject`, `s3:GetObject`
	and `s3:DeleteObject` on `.mikan-connection-tests/*`. Versioned buckets also need
	`s3:GetObjectVersion` and `s3:DeleteObjectVersion`. SSE-KMS buckets may require
	`kms:GenerateDataKey` and `kms:Decrypt` on the relevant KMS key.

Both credentials are encrypted using the existing integration Fernet key, never
returned by APIs and retained when both edit fields are blank. Replace both fields
together to rotate them. Back up `INTEGRATION_ENCRYPTION_KEY` or
`backend/.integration-key` securely and separately from database backups; losing
the key makes stored storage and Zoho credentials unusable. Do not regenerate it.

The separately confirmed test checks bucket access and writes, reads and deletes
a small random object under `.mikan-connection-tests/`. Provider request charges
can apply. Version IDs are used for cleanup when available; retention, object lock
or uncertain upload outcomes can leave an object/version requiring manual removal.
The latest cleanup warning, original bucket, endpoint and object key remain in
the edit dialog across reloads and later successful tests. This is a historical
warning, not proof the object still exists; only the latest warning is retained.
Deleting a connection removes its saved credentials and warning, never bucket data.
Record any outstanding cleanup details before removal. Tests have a 60-second
per-connection cooldown and bounded SDK timeouts.

| Method | Backend path | Purpose |
| --- | --- | --- |
| GET | `/integrations/storage` | Provider connection counts |
| GET | `/integrations/storage/{provider}` | List with search, UTC dates and ten-item pagination |
| POST | `/integrations/storage/{provider}` | Create a connection |
| PUT | `/integrations/storage/{provider}/{id}` | Edit configuration or rotate credentials |
| DELETE | `/integrations/storage/{provider}/{id}` | Remove saved configuration only |
| POST | `/integrations/storage/{provider}/{id}/test` | Test with JSON `{"confirm": true}` |

`provider` is `r2` or `s3`. All routes require super-admin authentication; mutations
also require an approved Origin. The same-origin `/api/admin/integrations/storage`
proxy allowlists routes and limits request bodies to 8 KB. Provider errors and
validation responses do not echo credentials. Automated tests mock provider calls;
real bucket permissions must still be verified using the explicit test action.

## Create Company Admins

Super admins can create company Admin accounts at `/admin/admins`. The five required
fields are full name, email, mobile, password (12-128 characters), and an existing
company. The role is always `admin`, not client-selectable. Email addresses are
case-insensitively unique across super-admin and company-admin accounts.

Creating an Admin sends no email, invitation, or OTP and does not depend on Zoho
configuration. Passwords use the existing salted scrypt hash and are never returned
by create/list responses. The form clears the password on close and successful save.

| Method | Backend path | Purpose |
| --- | --- | --- |
| GET | `/company-admins/` | List company Admins with search, UTC creation-date filters and pagination |
| POST | `/company-admins/` | Create from `name`, `email`, `mobile`, `password`, `company_id` |

Both endpoints require an active super-admin session; creation also requires an
approved Origin. The same-origin `/api/admin/company-admins` proxy limits bodies
to 8 KB and forwards to the trailing-slash backend collection. Duplicate emails
return 409; invalid input or a missing company returns 422. Existing accounts are
never overwritten. The database requires company linkage for the admin role and
prevents deleting a company while accounts reference it.

Company Admins sign in at `/company/admin/login` using the email and password set
by the super admin, then enter their protected `/company/admin` overview. The existing
`/admin/login` and all super-admin APIs continue to reject company Admins. Editing,
deleting, banning, invitations, and other member roles are outside this creation
step. Platform integrations remain super-admin managed; company-owned integrations
are a separate planned scope, not access to platform credentials.

## Company Admin Authentication

| Method | Backend path | Purpose |
| --- | --- | --- |
| POST | `/auth/company/admin/login` | Email/password sign-in, active `admin` role only |
| GET | `/auth/company/admin/me` | Current account and database-linked company identity |
| POST | `/auth/company/admin/logout` | Revoke company session and clear its cookie |

The browser uses `/api/company/admin/{login,me,logout}`. The proxy bounds login
bodies to 4 KB and checks approved mutation origins. Existing `ADMIN_PUBLIC_ORIGIN`,
`ADMIN_ORIGINS`, and `COOKIE_SECURE` settings apply to both login surfaces.

Company authentication reuses the scrypt verifier and five-attempt/15-minute
per-email throttle. Sessions use a separate `mikan_company_admin_session` cookie
(HttpOnly, SameSite=Strict, 30 days); super-admin and company sessions can
coexist. Both session checks verify the account's current active status, role, and
expiry. Company identity comes from the database, never a client-selected company.
Password hashes and integration credentials are not exposed. Login and logout
send no email. No additional database migration is needed beyond migration 004.

The company overview shows real account/company details, active team account counts,
pending invitations, Teams navigation and sign-out. Company integrations and admin
password reset remain separate work. Team accounts do not grant company-admin access.

## Teams and Invitations

Company admins manage their own teams at `/company/admin/teams#teams` and members
at `/company/admin/teams#members`. Tabs support direct links and browser history. Create a team,
then invite a person using full name, email, mobile, team and role (Team Manager
or Member). The invitation form shows the assigned manager. One non-disabled
manager per team is enforced in PostgreSQL, including pending invitations.
Each account belongs to one team; team-account email addresses are globally unique.
Teams and People lists include search, UTC creation-date filters and pagination.

Invitations use the enabled platform Zoho integration. On provider acceptance,
the success notice is "Member Invited successfully." A send failure rolls back the new account
and invitation. Activation links expire after 48 hours; resending invalidates the
old link. Revoking access disables the account and invalidates sessions, OTPs and
verification links. Revoked accounts cannot be edited or re-enabled.

The member edit action updates full name, email, mobile, team and role. Name/mobile
changes preserve sign-in access; changing team or role revokes sessions and pending
OTPs. Changing email revokes all existing access, resets the account to invited and
sends a fresh activation link to the new address. The recipient must verify that
address and choose a sign-in method again. A send failure rolls back the entire edit.
Only teams and members in the administrator's company can be edited, and the
one-manager constraint also applies when moving or promoting members.

Recipients open `/activate#token=...`, verify the invitation and choose email OTP
or a 12-128 character password. Tokens are hashed in the database, used once,
removed from the browser address bar and retained only in page memory. Reloading
requires reopening the email link. Activation does not automatically sign in.

Team sign-in is at `/`. The backend enforces the chosen method: password accounts
cannot receive sign-in OTPs and OTP accounts cannot use password login. OTPs expire
in 10 minutes, have a 60-second resend cooldown and a five-attempt limit. Invalid
or unknown accounts receive generic responses. Recovery links expire in one hour,
permit selecting a new method/password after email verification and revoke existing
sessions. Sessions use the separate 30-day HttpOnly/Strict `mikan_team_session`
cookie. `/team` shows My Profile for every signed-in team account. Only Team Managers
see My Team and can query active own-team names/roles, joined dates, charged
storage and ready-file counts. Their overview shows team allocation/usage,
active members, ready files and pending requests explicitly assigned to them.
Recent/largest file widgets and member drive links require the admin's
manager-viewing flag. Pending approvals link to the existing reviewer inbox;
being a manager does not grant permission to review other people's tasks.
Storage includes retained revisions and unreleased reservations/Trash; active
member rows may not sum to the team total when former members retain data.
Regular members see only
their own profile, do not request the directory, and receive HTTP 403 from
`/team/people` and `/team/dashboard`. Other members' contact details and
company-admin controls are not exposed.

Members and managers can edit their own name/mobile and upload, replace or remove a
profile photo from My Profile. Email, company, team, role and sign-in method remain
admin-managed. Saving is explicit; Cancel discards the draft. Photo uploads accept
JPEG/PNG/WebP up to 2 MiB, with a 16-megapixel/8192-pixel-side ceiling, and reuse
the image normalizer to apply orientation, discard metadata and create a WebP up to
512 pixels per side. The nullable `team_account.photo_data` column stores this
bounded account image, separately from drive files and quotas. Photo reads are
owner-session-only and private/no-store; there are no public or arbitrary-account
photo URLs. The frontend proxy bounds profile JSON to 2,800,000 bytes without
raising other routes' limits. Invalid images leave the existing profile unchanged.

| Method | Backend path | Purpose |
| --- | --- | --- |
| GET / POST | `/company/teams/` | List / create own-company teams |
| GET | `/company/teams/people` | List own-company accounts and active/invited totals |
| POST | `/company/teams/invite` | Create account and send activation email |
| POST | `/company/teams/people/{id}/edit` | Edit own-company member; reverify changed email |
| POST | `/company/teams/people/{id}/resend` | Replace a pending invitation |
| POST | `/company/teams/people/{id}/disable` | Revoke account access |
| POST | `/auth/team/verification` | Inspect an unexpired verification token |
| POST | `/auth/team/activate` | Activate or recover with selected authentication method |
| POST | `/auth/team/password` | Password sign-in |
| POST | `/auth/team/otp/request` | Request email OTP challenge |
| POST | `/auth/team/otp/verify` | Consume OTP and start session |
| POST | `/auth/team/recover` | Request account recovery email |
| GET / POST | `/auth/team/me`, `/auth/team/logout` | Read current identity / sign out |
| POST | `/team/profile` | Update own name/mobile and optional photo |
| GET | `/team/profile/photo` | Read own normalized private profile photo |
| GET | `/team/people` | Manager-only active own-team directory with usage, file counts, search/date/pagination |
| GET | `/team/dashboard` | Manager-only team usage, counts, assigned approvals and permission-gated file widgets |
| GET | `/team/files?owner_id={id}&folder=` | Permission-gated, read-only listing of an active own-team member drive |

Browser requests use `/api/company/teams` and `/api/team/auth/...` or
`/api/team/people`. Proxies allowlist routes, enforce mutation origins, bound JSON
bodies to 16 KB and forward only the relevant session cookie. Backend authorization
derives company/team identity from the database, with composite foreign keys and
transactional uniqueness constraints; client-selected company IDs are not accepted.

Before sending external invitations, configure a reachable HTTPS `TEAM_PUBLIC_ORIGIN`
matching the frontend `ADMIN_PUBLIC_ORIGIN` and backend `ADMIN_ORIGINS`. The default
localhost link works only on the development machine. Keep the backend private,
enable secure cookies and add trusted perimeter rate limits. Team sign-in does not
require client-IP header configuration. Without a signed identity, aggregate limits
use the backend connection address (shared by users behind the frontend), ignoring
caller-supplied IP headers. Account and OTP limits remain independent.

For optional per-client aggregate limits, configure the same
random `TEAM_PROXY_SECRET` (at least 32 characters) in the backend and frontend
server environments, never in `NEXT_PUBLIC_*`. Set frontend `TEAM_CLIENT_IP_HEADER`
to a lowercase header name that the trusted ingress **always overwrites** with one
verified client IP. It must not contain an unvalidated forwarded chain. Restrict
network access to Next to that ingress, and keep the backend inaccessible publicly.
Merely selecting `x-forwarded-for` without establishing this trust is unsafe.
Next signs the address, timestamp, method and backend path; the backend verifies
the signature within 30 seconds and uses the normalized IP for aggregate limits.
Keep server clocks synchronized. Leave `TEAM_CLIENT_IP_HEADER` empty to disable
signing, including in production. An existing backend secret alone does not require
signed requests. Explicitly enabled signing still rejects invalid configuration,
missing/invalid addresses, and invalid or expired signatures. Ordinary forwarded
headers are not trusted by the backend. Schedule retention cleanup of expired team
verification, OTP, session and old rate-limit records in production.

## Local Transfer Hardening

Migration020 binds multipart originals and revisions to `X-Upload-Fingerprint`:
64 lowercase hexadecimal characters. The browser starts with SHA-256 of
`mikan-upload-v1:{size}` and folds each 16 MiB chunk as SHA-256(previous digest
concatenated with SHA-256(chunk)). This requires reading the file before upload;
it is not standard whole-file SHA-256 or server-side verification of body bytes.
Changed-content retries fail before provider access. Existing multipart sessions
without a bound fingerprint must be cancelled and reserved again.

Upload body reception holds no database connection. Provider operations use short
authorization/publication transactions and a per-upload PostgreSQL advisory lock;
one idle autocommit connection is retained during provider I/O. Sessions are
rechecked after body reception and provider I/O. Database yield dependencies finish
before response streaming, so historical downloads no longer hold row locks for
the whole stream. Process crashes and lost database connections still require
real-provider concurrency and recovery testing; this is not distributed fencing.

Migration021 adds durable `cancelling`/`cancelled` states. Owners can cancel pending
originals; revision uploaders or file owners can cancel pending revisions, including
stale revisions, without requiring upload policy or Edit access to remain enabled.
POST `/team/files/{upload-id}/cancel` stops further upload operations, aborts exact-key
multipart sessions, removes exact-key objects and, on S3, their versions/delete
markers. Quota is released only after provider absence is confirmed. Failed cleanup
returns 503, retains quota and allows retry. Cancellation is idempotent after success;
ready files cannot be cancelled. Tombstones retain retry identity but are excluded
from active files, derived folders and storage reports. Revision history retains them.

Credentials need permission to list multipart uploads, abort them, delete objects and
confirm absence via HEAD. S3 also requires listing object versions and deleting object
versions (`s3:ListBucketMultipartUploads`, `s3:AbortMultipartUpload`,
`s3:DeleteObject`, `s3:GetObject`, `s3:ListBucket`, `s3:ListBucketVersions`,
`s3:DeleteObjectVersion`, alongside existing upload permissions). Object Lock,
retention policies, missing permissions or uncertain provider responses retain quota.
Cleanup never deletes neighboring prefix keys. There is no automatic stale-upload
sweeper; cancellation is explicit. Active queue uploads must finish their attempt
before cancellation. Provider lifecycle cleanup alone does not release database quota.

Local verification does not replace authenticated browser, real S3/R2 large-transfer,
crash/concurrency, fresh-install, backup/restore and production-build verification.
The current npm production audit is clean. The development-only Next ESLint chain
still reports five high findings from the single braces stack-exhaustion advisory
GHSA-vfj7-8cjw-p6xm; no patched braces/micromatch release was available when checked.
Do not feed untrusted glob patterns to lint tooling. No forced Next ESLint downgrade
has been applied; monitor the upstream fix before closing this remaining audit item.

## Trash And Retention

Migration022 adds soft deletion and company-controlled retention. In My Files,
owners use **Move to Trash** and the **Trash** tab to restore their own deletions.
Company administrators use **Data > Trash** to restore owner/admin deletions and
configure automatic cleanup for 1-3650 days. Owners cannot undo admin deletions.
Workflow-managed trash keeps its existing restoration restrictions.

Cleanup defaults **disabled**, with a suggested retention of 30 days. No existing
company policy is enabled by migration. Saving an enabled policy applies it to
existing and future trash, measured from `trashed_at`; shortening retention can
make old trash eligible immediately. Existing trashed rows use their admin-trash
timestamp when available, otherwise migration time. Restoring and deleting again
starts a new retention period. Restore reinstates the original location and sharing.
Trash blocks new downloads, but cannot recall an already-running download.

The existing worker requires `MIKAN_AUTOMATION_ENABLED=true`. It selects up to ten
eligible files per cycle, with a 15-second pause between cycles; processing time,
backlogs and provider outages mean cleanup is not an exact deadline. Email and
automation failures do not skip the trash callback. Owner endpoints are
`POST /team/files/{id}/trash`, `POST /team/files/{id}/restore` and
`GET /team/files/trash`; company policy uses GET/POST
`/company/teams/data/trash-settings`. There is no immediate permanent-delete API.

Cleanup first persists **Cleaning** (`purging`) and locks the original plus all
revision upload operations. Restoration is then blocked, even after provider
failure. Disabling or extending retention stops new claims, not cleanup already
started. Exact-key multipart sessions, historical revisions and current objects
are removed across their original storage connections, including disabled ones.
S3 object versions/delete markers are included. The cleanup permissions and
provider limitations documented above also apply here.

Quota remains charged until all objects are confirmed absent. Failure keeps the
Cleaning state and a visible retry message; attempts are spaced at least 15 minutes
from the previous claim. Success releases quota once and hides the purged file
from file/trash listings and storage counts. Database tombstones and audit metadata
remain for referential integrity; retained metadata is not downloadable content.
Storage-provider Object Lock or missing permissions can prevent cleanup indefinitely.
Real-provider recovery, connection-loss/concurrency and browser verification remain
deployment gates; local tests use rolled-back fixtures and mocked storage.

## ZeptoMail / Zoho CPaaS

ZeptoMail is now **Zoho CPaaS**. Existing ZeptoMail API tokens and verified sending
domains are preserved, according to [Zoho's announcement](https://www.zoho.com/cpaas/).
The email channel remains labeled ZeptoMail in Mikan.

Open `/admin/integrations` and select the ZeptoMail card, or open
`/admin/integrations/zeptomail`. These are platform-wide settings available only
to an active super admin. No company administrator access is granted.

1. Verify a sending domain in Zoho CPaaS.
2. Open the Agent's **SMTP/API** tab and obtain its email API key.
3. Select the matching account data center in Mikan: India, US, Europe, Australia,
	Japan, or China. The UI displays the corresponding approved API endpoint.
4. Enter the API token, sender name and verified sender email, then save.
	Tokens can be pasted raw or with the `Zoho-enczapikey ` prefix.
5. Enter a recipient and explicitly select **Send test email** to request a real
	transactional message. Saving settings never sends mail. A successful response
	means provider acceptance, not confirmed inbox delivery.

The enabled flag gates team invitations, email OTP and account recovery. Test
sending is explicitly permitted while disabled so configuration can be checked first.
Unchanged credentials are retained when the token field is left blank. Removal
deletes the stored token and sender settings in Mikan, not the Zoho account/key.

| Method | Backend path | Purpose |
| --- | --- | --- |
| GET | `/integrations/zeptomail` | Read public settings/status and approved endpoints, never the token |
| PUT | `/integrations/zeptomail` | Save sender, endpoint, enabled flag, optional replacement token |
| POST | `/integrations/zeptomail/test` | Send a fixed test email to `recipient` using saved settings |
| DELETE | `/integrations/zeptomail` | Remove stored credentials and settings |

The browser uses the matching `/api/admin/integrations/zeptomail` routes.
The Next proxy limits request bodies to 8 KB, requires approved mutation origins,
and returns uncached responses. The backend repeats session/role/origin checks,
restricts sending to the six documented HTTPS Zoho endpoints, disables redirects
and environment proxy inheritance, and uses a 10-second provider timeout. Test
attempts record a sanitized result with a 60-second cooldown; saves reset test
status. Raw provider errors and API tokens are never included in API responses.

API tokens are Fernet-encrypted in PostgreSQL. A random local key was generated
at `backend/.integration-key` with owner-only permissions and is ignored by Git.
Do not delete or regenerate it after credentials have been saved. Back it up
securely alongside (but separately from) database backups. Losing it requires
re-entering the Zoho API token. Never include the key file in source control or
public artifacts. Production should provide `INTEGRATION_ENCRYPTION_KEY` through
its secret manager; changing this key without re-encrypting data breaks decryption.
No key is automatically generated during a request.

Official references: [authentication](https://www.zoho.com/cpaas/help/api/api-authentication.html),
[data centers](https://www.zoho.com/cpaas/help/api/multiple-data-centers.html),
[send email](https://www.zoho.com/cpaas/help/api/email-sending.html).

## Tests

```sh
.venv/bin/python -m unittest discover -s backend -p test_auth.py -v
```

Tests require the migrated local database. Test records are rolled back and existing
accounts are not modified. The suite covers authentication, company authorization,
validation, create/edit, logo decoding and removal, search, dates, pagination, and
integration credential encryption/redaction, retention, permissions, test-send
acceptance/failure, throttling, and removal. Company Admin tests cover password
hashing/redaction, company assignment, duplicate email, validation, listing,
super-admin access isolation, and no email calls on creation. The 19-test suite also
covers team/company isolation, manager uniqueness, activation/resend/recovery,
mail-failure rollback, method enforcement, OTP expiry/reuse/attempt limits,
revocation and origin rejection. Provider calls are mocked; the suite
sends no real email. Pillow handles image validation and cryptography handles
credential encryption; `backend/requirements.txt` contains the frozen dependencies.