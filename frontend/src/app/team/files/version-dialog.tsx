"use client";

import { useEffect, useRef, useState } from "react";
import { Activity as ActivityIcon, Download, FileText, History as HistoryIcon, RefreshCw, RotateCcw, Upload, X } from "lucide-react";
import { MAX_FILE_BYTES, teamRequest, uploadTeamFile } from "@/lib/team-client";
import { dateTime, LoadState, Pagination, useResource } from "../../company/admin/workflows/workflow-ui";
import shared from "../../admin/companies/companies.module.css";
import drive from "./files.module.css";

type Version = { id: string; number: number; name: string; size_bytes: number; state: string; created_at: string; uploaded_at: string | null; uploaded_by: string; own_upload: boolean; base_version: number };
type History = { items: Version[]; total: number; current_version: number; can_edit: boolean; is_owner: boolean; policy: { enabled: boolean; max_file_bytes: number } | null };
type Activity = { items: { id: number; actor: string; action: string; detail: string; created_at: string }[]; total: number };
const activityTitles: Record<string, string> = { version_uploaded: "Version uploaded", version_restored: "Version restored", access_granted: "Access granted", permission_changed: "Permission changed", access_removed: "Access removed", link_access: "Link access changed" };
const bytes = (size: number) => size >= 1024 ** 4 ? `${(size / 1024 ** 4).toFixed(2)} TiB` : size >= 1024 ** 3 ? `${(size / 1024 ** 3).toFixed(2)} GiB` : size >= 1024 ** 2 ? `${(size / 1024 ** 2).toFixed(1)} MiB` : `${size.toLocaleString()} B`;

export default function VersionDialog({ file, onClose, onChanged }: { file: { id: string; name: string }; onClose: () => void; onChanged: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const reservation = useRef<{ key: string; id?: string; base: number } | null>(null);
  const [selected, setSelected] = useState<File | null>(null);
  const [retry, setRetry] = useState<Version | null>(null);
  const [revision, setRevision] = useState(0);
  const [page, setPage] = useState(1);
  const [activityPage, setActivityPage] = useState(1);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [restore, setRestore] = useState<Version | null>(null);
  const [cancel, setCancel] = useState<Version | null>(null);
  const [tab, setTab] = useState<"versions" | "activity">("versions");
  const base = `/api/team/files/${file.id}`;
  const dates = new URLSearchParams({ ...(from ? { from_date: from } : {}), ...(to ? { to_date: to } : {}) });
  const history = useResource<History>(`${base}/versions?page=${page}&${dates}`, false, revision);
  const activity = useResource<Activity>(`${base}/activity?page=${activityPage}&${dates}`, false, revision);
  const canUpload = !!history.data?.can_edit && !!history.data.policy?.enabled;
  const limit = Math.min(history.data?.policy?.max_file_bytes ?? MAX_FILE_BYTES, MAX_FILE_BYTES);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  useEffect(() => {
    if (!busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  function choose(incoming: File | undefined) {
    if (!incoming) return;
    setError(""); setMessage("");
    if (incoming.name !== file.name || (retry && incoming.size !== retry.size_bytes)) { setError(retry ? "Select the original revision file with the same name and size." : "Select a revised file with the same filename."); return; }
    if (incoming.size > limit) { setError(`File exceeds the ${bytes(limit)} upload limit.`); return; }
    setSelected(incoming); reservation.current = retry ? { key: "", id: retry.id, base: retry.base_version } : null;
  }

  async function upload() {
    if (!selected || !canUpload || !history.data || busy) return;
    setBusy(true); setError(""); setMessage(""); setProgress(0);
    try {
      const request = reservation.current ?? { key: crypto.randomUUID(), base: history.data.current_version };
      reservation.current = request;
      if (!request.id) {
        const result = await teamRequest<{ id: string }>(`${base}/versions`, { name: selected.name, size_bytes: selected.size, upload_key: request.key, base_version: request.base });
        request.id = result.id;
      }
      await uploadTeamFile(request.id, selected, setProgress);
      setSelected(null); setRetry(null); reservation.current = null;
      setPage(1); setActivityPage(1); setRevision(value => value + 1); onChanged(); setMessage("New version uploaded.");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to upload revision."); }
    finally { setBusy(false); setRevision(value => value + 1); }
  }

  async function cancelVersion() {
    if (!cancel || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await teamRequest(`/api/team/files/${cancel.id}/cancel`, {}, undefined, 300000);
      if (reservation.current?.id === cancel.id || retry?.id === cancel.id) { setSelected(null); setRetry(null); reservation.current = null; }
      setCancel(null); onChanged(); setMessage("Revision upload cancelled.");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to cancel revision."); }
    finally { setBusy(false); setRevision(value => value + 1); }
  }

  async function restoreVersion() {
    if (!restore || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await teamRequest(`${base}/versions/${restore.id}/restore`, {});
      setRestore(null); setPage(1); setActivityPage(1); setRevision(value => value + 1); onChanged(); setMessage("Version restored.");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to restore version."); }
    finally { setBusy(false); }
  }

  return <dialog ref={dialog} className={`${shared.dialog} ${drive.versionDialog}`} aria-labelledby="version-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header className={`${shared.dialogHeader} ${drive.versionHeader}`}>
      <div><h2 id="version-title">Version history</h2><div className={drive.versionFile}><FileText size={18} aria-hidden="true" /><span>{file.name}</span></div></div>
      <button type="button" className={shared.iconButton} disabled={busy} title="Close" aria-label="Close version history" onClick={onClose}><X size={18} /></button>
    </header>
    <div className={drive.versionTabs} role="tablist" aria-label="File history">
      {(["versions", "activity"] as const).map(value => <button key={value} type="button" role="tab" id={`file-${value}-tab`} aria-controls={`file-${value}-panel`} aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} disabled={busy} onClick={() => { setTab(value); setRestore(null); }} onKeyDown={event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? "versions" : event.key === "End" ? "activity" : value === "versions" ? "activity" : "versions";
        setTab(next); setRestore(null);
        event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#file-${next}-tab`)?.focus();
      }}>{value === "versions" ? <HistoryIcon size={17} /> : <ActivityIcon size={17} />}{value === "versions" ? "Versions" : "Activity"}</button>)}
    </div>
    <div className={drive.versionBody}>
      {error && <p role="alert" className={shared.formError}>{error}</p>}
      {message && <p role="status" className={shared.notice}>{message}</p>}
      {tab === "versions" && history.data?.can_edit && <section className={drive.versionUpload} aria-label="Revised file upload">
        <div className={drive.versionSectionHeading}><h3>{retry ? `Retry version ${retry.number}` : "New version"}</h3><span>Limit {bytes(limit)}</span></div>
        {!history.data.policy?.enabled && <p role="status">Uploads are disabled by your company.</p>}
        <div className={drive.versionTools}><button type="button" className={shared.secondary} disabled={busy || !canUpload} onClick={() => picker.current?.click()}><Upload size={16} />{selected ? "Change file" : "Choose revised file"}</button><input ref={picker} hidden type="file" aria-label="Choose revised file" disabled={busy || !canUpload} onChange={event => { choose(event.target.files?.[0]); event.target.value = ""; }} />
          {selected && <button type="button" className={shared.primary} disabled={busy || !canUpload} onClick={() => void upload()}><Upload size={16} />{busy ? "Uploading..." : "Upload version"}</button>}
          {(selected || retry) && <button type="button" className={shared.iconButton} disabled={busy} title="Clear selection" aria-label="Clear revision selection" onClick={() => { setSelected(null); setRetry(null); reservation.current = null; }}><X size={16} /></button>}
        </div>
        {(selected || retry) && <dl className={drive.versionSelection}><div><dt>{selected ? "Selected file" : "Required file"}</dt><dd>{selected?.name ?? file.name}</dd></div><div><dt>Size</dt><dd>{bytes(selected?.size ?? retry!.size_bytes)}</dd></div></dl>}
        {busy && selected && <div className={drive.versionProgress}><span role="status">Uploading revision <strong>{progress}%</strong></span><progress max={100} value={progress} aria-label="Revision upload progress" /></div>}
      </section>}
      <div className={drive.versionFilters} aria-label="History date range">
        <label className={drive.filterField}>From<input type="date" value={from} max={to || undefined} disabled={busy} onChange={event => { setFrom(event.target.value); setPage(1); setActivityPage(1); setRestore(null); }} /></label>
        <label className={drive.filterField}>To<input type="date" value={to} min={from || undefined} disabled={busy} onChange={event => { setTo(event.target.value); setPage(1); setActivityPage(1); setRestore(null); }} /></label>
        <button type="button" className={shared.iconButton} disabled={busy || history.loading || activity.loading} title="Refresh history" aria-label="Refresh version history" onClick={() => setRevision(value => value + 1)}><RefreshCw size={17} /></button>
      </div>
      <section id={`file-${tab}-panel`} role="tabpanel" aria-labelledby={`file-${tab}-tab`} tabIndex={0}>
        {tab === "versions" ? <>
          <div className={drive.versionSectionHeading}><h3>File versions{history.data ? ` (${history.data.total})` : ""}</h3>{history.data && <span>Current: v{history.data.current_version}</span>}</div>
          <LoadState {...history} retry={() => setRevision(value => value + 1)} />
          {history.data && <>
            <ul className={drive.versionList} aria-label="File versions">{history.data.items.map(version => <li key={version.id}>
              <div className={drive.versionRow}>
                <div className={drive.versionDetails}>
                  <div className={drive.versionRowTitle}><strong>Version {version.number}</strong><span className={drive.versionBadge} data-state={version.number === history.data!.current_version ? "current" : version.state}>{version.number === history.data!.current_version ? "Current" : version.state === "pending" ? "Pending upload" : version.state === "cancelling" ? "Cleanup pending" : version.state === "cancelled" ? "Cancelled" : "Previous"}</span></div>
                  <dl className={drive.versionMetadata}>
                    <div><dt>{version.state === "pending" ? "Started by" : "Added by"}</dt><dd>{version.uploaded_by}</dd></div>
                    <div><dt>Size</dt><dd>{bytes(version.size_bytes)}</dd></div>
                    <div><dt>{version.state === "pending" ? "Started" : "Added"}</dt><dd><time dateTime={version.uploaded_at || version.created_at}>{dateTime(version.uploaded_at || version.created_at)}</time></dd></div>
                  </dl>
                  {version.state === "pending" && version.base_version !== history.data!.current_version && <p className={drive.versionPending}>Based on v{version.base_version}. A newer version is current; this upload cannot resume.</p>}
                </div>
                <div className={drive.versionActions}>
                  {["pending", "cancelling"].includes(version.state) && (version.own_upload || history.data!.is_owner) && <button type="button" className={shared.iconButton} disabled={busy} title={version.state === "cancelling" ? "Retry cleanup" : "Cancel revision upload"} aria-label={`Cancel revision ${version.number} upload`} onClick={() => { setRestore(null); setCancel(version); }}><X size={17} /></button>}
                  {version.state === "ready" && <a className={shared.iconButton} href={`${base}/versions/${version.id}/content`} download title={`Download version ${version.number}`} aria-label={`Download version ${version.number}`}><Download size={17} /></a>}
                  {version.state === "ready" && history.data!.is_owner && history.data!.can_edit && version.number !== history.data!.current_version && <button type="button" className={shared.iconButton} disabled={busy} title={`Restore version ${version.number}`} aria-label={`Restore version ${version.number}`} onClick={() => setRestore(version)}><RotateCcw size={17} /></button>}
                  {version.state === "pending" && version.own_upload && canUpload && version.base_version === history.data!.current_version && <button type="button" className={shared.secondary} disabled={busy} onClick={() => { setRetry(version); setSelected(null); reservation.current = null; setError(""); dialog.current?.querySelector(`.${drive.versionBody}`)?.scrollTo({ top: 0, behavior: "smooth" }); }}><Upload size={16} />Retry</button>}
                </div>
              </div>
              {cancel?.id === version.id && <div className={drive.versionRestore} role="group" aria-label="Confirm upload cancellation"><strong>Cancel version {cancel.number} upload?</strong><p>Reserved storage is released after cleanup is confirmed. Completed versions are retained.</p><div className={drive.versionTools}><button type="button" className={shared.secondary} disabled={busy} onClick={() => setCancel(null)}>Keep upload</button><button type="button" className={shared.primary} disabled={busy} onClick={() => void cancelVersion()}><X size={16} />{busy ? "Cleaning up..." : "Cancel upload"}</button></div></div>}
              {restore?.id === version.id && <div className={drive.versionRestore} role="group" aria-label="Confirm version restore"><strong>Restore version {restore.number}?</strong><p>This becomes the current file as a new version. Existing versions are retained.</p><div className={drive.versionTools}><button type="button" className={shared.secondary} disabled={busy} onClick={() => setRestore(null)}>Cancel</button><button type="button" className={shared.primary} disabled={busy} onClick={() => void restoreVersion()}><RotateCcw size={16} />{busy ? "Restoring..." : "Restore"}</button></div></div>}
            </li>)}</ul>
            {!history.data.items.length && <div className={drive.versionEmpty}><HistoryIcon size={24} /><p>No versions in this date range.</p></div>}
            {history.data.total > 10 && <Pagination total={history.data.total} page={page} setPage={value => { setPage(value); setRestore(null); }} />}
          </>}
        </> : <>
          <div className={drive.versionSectionHeading}><h3>File activity{activity.data ? ` (${activity.data.total})` : ""}</h3></div>
          <LoadState {...activity} retry={() => setRevision(value => value + 1)} />
          {activity.data && <>
            <ol className={drive.versionActivity} aria-label="File activity">{activity.data.items.map(event => <li key={event.id}>
              <span className={drive.versionActivityIcon} aria-hidden="true"><ActivityIcon size={17} /></span>
              <div><strong>{activityTitles[event.action] || "File updated"}</strong><p>{event.detail}</p><div className={drive.versionActivityMeta}><span>{event.actor}</span><time dateTime={event.created_at}>{dateTime(event.created_at)}</time></div></div>
            </li>)}</ol>
            {!activity.data.items.length && <div className={drive.versionEmpty}><ActivityIcon size={24} /><p>No activity in this date range.</p></div>}
            {activity.data.total > 10 && <Pagination total={activity.data.total} page={activityPage} setPage={setActivityPage} />}
          </>}
        </>}
      </section>
    </div>
    <footer className={shared.dialogFooter}><button type="button" className={shared.secondary} disabled={busy} onClick={onClose}>Done</button></footer>
  </dialog>;
}