"use client";

import { useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronRight, Download, File, Folder, FolderPlus, History, Pencil, RefreshCw, RotateCcw, Save, Trash2, X } from "lucide-react";
import { teamRequest, TeamRequestError } from "@/lib/team-client";
import { dateTime, Filters, LoadState, Pagination, useResource } from "../workflows/workflow-ui";
import shared from "../../../admin/companies/companies.module.css";
import styles from "./data.module.css";

type Drive = { team_id: number; owner_id: number; team_name: string; owner_name: string };
type FileRow = Drive & { id: string; name: string; folder: string; size_bytes: number; state: "pending" | "ready" | "quarantined" | "trashed" | "purging" | "cancelling"; created_at: string; trashed_at: string | null; purge_error: string | null; restorable: boolean };
type Retention = { enabled: boolean; retention_days: number };
type FolderRow = Drive & { path: string; created_at: string };
type EventRow = { id: number; actor: string; action: string; subject: string; detail: string; created_at: string };
type Person = { id: number; name: string; team_id: number; team_name: string; email: string };
type View = "files" | "folders" | "trash" | "activity";
type RootRow = { id: string; name: string; path: string; kind: "folder" | "file"; size_bytes: number; state: "pending" | "ready"; created_at: string };
type Edit = { kind: "file"; action: "edit" | "trash" | "restore"; file: FileRow } | { kind: "folder"; action: "create" | "rename" | "remove"; folder?: FolderRow };
const base = "/api/company/teams/data";
const bytes = (value: number) => value >= 1073741824 ? `${(value / 1073741824).toFixed(2)} GB` : value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : value >= 1024 ? `${(value / 1024).toFixed(1)} KB` : `${value} B`;
const actionNames: Record<string, string> = { file_edit: "File renamed / moved", file_trash: "File trashed", file_restore: "File restored", folder_create: "Folder created", folder_rename: "Folder renamed", folder_remove: "Empty folder removed", download_requested: "Download requested" };

function RetentionForm({ policy, onSaved }: { policy: Retention; onSaved: () => void }) {
  const [enabled, setEnabled] = useState(policy.enabled);
  const [days, setDays] = useState(String(policy.retention_days));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  async function save() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await teamRequest(`${base}/trash-settings`, { enabled, retention_days: Number(days) });
      dialog.current?.close(); onSaved();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to save retention."); }
    finally { setBusy(false); }
  }
  return <>
    <form className={styles.retention} onSubmit={event => { event.preventDefault(); setError(""); dialog.current?.showModal(); }}>
      <label className={styles.retentionToggle}><input type="checkbox" checked={enabled} disabled={busy} onChange={event => setEnabled(event.target.checked)} />Automatic trash cleanup</label>
      <label className={shared.field}>Retention (days)<input type="number" min={1} max={3650} step={1} required value={days} disabled={busy} onChange={event => setDays(event.target.value)} /></label>
      <button className={shared.primary} disabled={busy || (enabled === policy.enabled && Number(days) === policy.retention_days)}><Save size={16} />Save</button>
    </form>
    <dialog ref={dialog} className={shared.dialog} aria-labelledby="retention-title" onCancel={event => { if (busy) event.preventDefault(); }}>
      <header className={shared.dialogHeader}><h2 id="retention-title">Save trash retention?</h2><button type="button" className={shared.iconButton} title="Close" aria-label="Close" disabled={busy} onClick={() => dialog.current?.close()}><X size={18} /></button></header>
      <div className={shared.formBody}><p className={styles.warning}>{enabled ? `Files already in Trash and future deletions will be permanently removed, including all versions, after ${days} days in Trash. Files already older than this may be cleaned on the next worker cycle.` : "New automatic cleanups will stop. Files in Trash remain stored and continue to use quota."}</p><p className={styles.warning}>Cleanup already started cannot be stopped or restored. Storage is released only after removal is confirmed.</p>{error && <p className={shared.formError} role="alert">{error}</p>}</div>
      <footer className={shared.dialogFooter}><button type="button" className={shared.secondary} disabled={busy} onClick={() => dialog.current?.close()}>Cancel</button><button type="button" className={shared.primary} disabled={busy} onClick={() => void save()}><Save size={16} />{busy ? "Saving..." : "Confirm policy"}</button></footer>
    </dialog>
  </>;
}

function RetentionSettings() {
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState("");
  const state = useResource<Retention>(`${base}/trash-settings`, true, revision);
  return <div><LoadState {...state} retry={() => setRevision(value => value + 1)} />{notice && <p role="status" className={shared.notice}>{notice}</p>}{state.data && <RetentionForm key={revision} policy={state.data} onSaved={() => { setNotice("Trash retention saved."); setRevision(value => value + 1); }} />}</div>;
}

function DrivePicker({ selected, onChange }: { selected: Drive | null; onChange: (drive: Drive) => void }) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const state = useResource<{ items: Person[]; total: number }>(`/api/company/teams/people?search=${encodeURIComponent(search)}&page=${page}&page_size=10`, true, revision);
  return <fieldset className={styles.picker}><legend>Employee drive</legend>
    <input aria-label="Search employee drives" placeholder="Search employees" maxLength={100} value={search} onChange={event => { setSearch(event.target.value); setPage(1); }} />
    {selected && <p className={styles.selection}>{selected.owner_name} / {selected.team_name}</p>}
    <LoadState loading={state.loading} error={state.error} retry={() => setRevision(value => value + 1)} />
    {state.data && <><ul>{state.data.items.map(person => <li key={person.id}><label><input type="radio" name="drive" checked={selected?.owner_id === person.id && selected.team_id === person.team_id} onChange={() => onChange({ owner_id: person.id, owner_name: person.name, team_id: person.team_id, team_name: person.team_name })} /><span><strong>{person.name}</strong><small>{person.team_name} / {person.email}</small></span></label></li>)}</ul>{!state.data.total && <p>No employees found.</p>}<Pagination total={state.data.total} page={page} setPage={setPage} /></>}
  </fieldset>;
}

function DataViews({ view, onNavigate }: { view: View | "root"; onNavigate?: () => void }) {
  return <nav className={styles.tabs} aria-label="Data views">{([{ key: "root", label: "Company root", icon: Folder }, { key: "files", label: "Files", icon: File }, { key: "folders", label: "Folders", icon: Folder }, { key: "trash", label: "Trash", icon: Trash2 }, { key: "activity", label: "Activity", icon: History }] as const).map(tab => <Link key={tab.key} href={`/company/admin/data?view=${tab.key}`} scroll={false} aria-current={view === tab.key ? "page" : undefined} onNavigate={onNavigate}><tab.icon size={16} />{tab.label}</Link>)}</nav>;
}

export default function DataManager({ view, folder }: { view: View | "root"; folder: string }) {
  return view === "root" ? <CompanyRoot key={folder} folder={folder} /> : <DriveDataManager view={view} />;
}

function CompanyRoot({ folder }: { folder: string }) {
  const [search, setSearch] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const params = new URLSearchParams({ folder, search, page: String(page) });
  if (from) params.set("from_date", from);
  if (to) params.set("to_date", to);
  const state = useResource<{ items: RootRow[]; total: number }>(`${base}/root?${params}`, true, revision);
  const folderHref = (path: string) => `/company/admin/data?view=root&folder=${encodeURIComponent(path)}`;
  function open() { setName(""); setError(""); dialog.current?.showModal(); }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try {
      await teamRequest(`${base}/root/folders`, { name, parent: folder });
      dialog.current?.close(); setPage(1); setSearch(""); setFrom(""); setTo("");
      setNotice("Folder created."); setRevision(value => value + 1);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to create folder."); }
    finally { setBusy(false); }
  }
  function entryName(entry: RootRow) {
    return entry.kind === "folder" ? <Link className={styles.folderLink} href={folderHref(entry.path)}><Folder size={18} />{entry.name}</Link> : <strong>{entry.name}</strong>;
  }
  function download(entry: RootRow) {
    return entry.kind === "file" && entry.state === "ready" ? <a className={shared.iconButton} href={`${base}/root/files/${entry.id}/content`} title="Download file" aria-label={`Download ${entry.name}`}><Download size={17} /></a> : null;
  }
  return <section className={`${shared.section} ${styles.section}`}>
    <div className={styles.heading}><h1>Data Administration</h1><div className={styles.actions}><button className={shared.iconButton} title="Refresh" aria-label="Refresh company root" onClick={() => setRevision(value => value + 1)}><RefreshCw size={18} /></button><button className={shared.primary} onClick={open}><FolderPlus size={17} />New folder</button></div></div>
    <DataViews view="root" />
    <nav className={styles.breadcrumb} aria-label="Folder location"><Link href={folderHref("")}>Company root</Link>{folder && folder.split("/").map((part, index, parts) => <Link key={parts.slice(0, index + 1).join("/")} href={folderHref(parts.slice(0, index + 1).join("/"))}><ChevronRight size={14} />{part}</Link>)}</nav>
    {notice && <p className={shared.notice} role="status">{notice}</p>}
    <Filters search={search} setSearch={value => { setSearch(value); setPage(1); }} from={from} setFrom={value => { setFrom(value); setPage(1); }} to={to} setTo={value => { setTo(value); setPage(1); }} />
    <LoadState loading={state.loading} error={state.error} retry={() => setRevision(value => value + 1)} />
    {state.data && !state.data.items.length && <div className={shared.empty}><Folder size={30} /><h2>{search || from || to ? "No matching items" : "This folder is empty"}</h2><button className={shared.primary} onClick={open}><FolderPlus size={17} />New folder</button></div>}
    {!!state.data?.items.length && <><div className={styles.desktop}><table className={styles.table}><thead><tr><th>Name</th><th>Type</th><th>Size / Status</th><th>Added on</th><th>Actions</th></tr></thead><tbody>{state.data.items.map(entry => <tr key={entry.id}><td>{entryName(entry)}</td><td>{entry.kind === "folder" ? "Folder" : "File"}</td><td>{entry.kind === "file" ? bytes(entry.size_bytes) : ""}{entry.state === "pending" && <small className={styles.badge}>Import pending</small>}</td><td>{dateTime(entry.created_at)}</td><td>{download(entry)}</td></tr>)}</tbody></table></div><div className={styles.mobile}>{state.data.items.map(entry => <article className={styles.item} key={entry.id}><header>{entryName(entry)}</header><dl>{entry.kind === "file" && <div><dt>Size</dt><dd>{bytes(entry.size_bytes)}</dd></div>}<div><dt>Added on</dt><dd>{dateTime(entry.created_at)}</dd></div>{entry.state === "pending" && <div><dt>Status</dt><dd>Import pending</dd></div>}</dl>{download(entry)}</article>)}</div></>}
    {state.data && <Pagination total={state.data.total} page={page} setPage={setPage} />}
    <dialog ref={dialog} className={shared.dialog} aria-labelledby="root-folder-title" onCancel={event => { if (busy) event.preventDefault(); }}><form onSubmit={save}><header className={shared.dialogHeader}><h2 id="root-folder-title">Create folder</h2><button type="button" className={shared.iconButton} title="Close" aria-label="Close dialog" disabled={busy} onClick={() => dialog.current?.close()}><X size={18} /></button></header><fieldset className={shared.formBody} disabled={busy}><p className={styles.selection}>{folder || "Company root"}</p><label className={shared.field}>Folder name<input required maxLength={255} value={name} onChange={event => setName(event.target.value)} /></label>{error && <p className={shared.formError} role="alert">{error}</p>}</fieldset><footer className={shared.dialogFooter}><button type="button" className={shared.secondary} disabled={busy} onClick={() => dialog.current?.close()}>Cancel</button><button className={shared.primary} disabled={busy}><Save size={16} />{busy ? "Saving..." : "Create"}</button></footer></form></dialog>
  </section>;
}

function DriveDataManager({ view }: { view: View }) {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [location, setLocation] = useState<FolderRow | null>(null);
  const [notice, setNotice] = useState("");
  const [edit, setEdit] = useState<Edit | null>(null);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [drive, setDrive] = useState<Drive | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const params = new URLSearchParams({ search, from_date: from, to_date: to, page: String(page) });
  if (!from) params.delete("from_date");
  if (!to) params.delete("to_date");
  if (view === "trash") params.set("view", "trash");
  if (location && (view === "files" || view === "trash")) {
    params.set("team_id", String(location.team_id)); params.set("owner_id", String(location.owner_id)); params.set("folder", location.path);
  }
  const state = useResource<{ items: (FileRow | FolderRow | EventRow)[]; total: number }>(`${base}/${view === "trash" ? "files" : view}?${params}`, true, revision);
  const fileRows = view === "files" || view === "trash" ? state.data?.items as FileRow[] | undefined : undefined;
  const folders = view === "folders" ? state.data?.items as FolderRow[] | undefined : undefined;
  const events = view === "activity" ? state.data?.items as EventRow[] | undefined : undefined;
  const title = edit?.kind === "file" ? ({ edit: "Rename or move file", trash: "Move file to trash", restore: "Restore file" }[edit.action]) : edit ? ({ create: "Create folder", rename: "Rename folder", remove: "Remove empty folder" }[edit.action]) : "";

  function open(next: Edit) {
    setEdit(next); setError("");
    setName(next.kind === "file" ? next.file.name : "");
    setPath(next.kind === "file" ? next.file.folder : next.folder?.path || "");
    setDrive(next.kind === "folder" ? next.folder || location : null);
    dialog.current?.showModal();
  }
  function close() { if (!busy) { dialog.current?.close(); setEdit(null); } }
  function browse(folder: FolderRow) { setLocation(folder); if (view !== "files") router.push("/company/admin/data?view=files", { scroll: false }); setPage(1); setSearch(""); setFrom(""); setTo(""); }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!edit || busy) return;
    if (edit.kind === "folder" && !drive) { setError("Select an employee drive."); return; }
    setBusy(true); setError("");
    try {
      if (edit.kind === "file") {
        await teamRequest(`${base}/files/${edit.file.id}`, { action: edit.action, name, folder: path, expected_name: edit.file.name, expected_folder: edit.file.folder, expected_state: edit.file.state });
      } else {
        await teamRequest(`${base}/folders`, { action: edit.action, team_id: drive!.team_id, owner_id: drive!.owner_id, path: edit.action === "create" ? path : edit.folder!.path, target: edit.action === "rename" ? path : "" });
      }
      setNotice(edit.kind === "file" ? "File updated." : "Folder updated.");
      dialog.current?.close(); setEdit(null); setPage(1); setRevision(value => value + 1);
    } catch (failure) {
      if (failure instanceof TeamRequestError && failure.status === 401) { router.replace("/company/admin/login"); router.refresh(); }
      setError(failure instanceof Error ? failure.message : "Unable to save.");
    } finally { setBusy(false); }
  }
  function fileActions(file: FileRow) {
    return <div className={styles.actions}>{file.state === "ready" ? <>
      <a className={shared.iconButton} href={`${base}/files/${file.id}/content`} title="Download file" aria-label={`Download ${file.name}`}><Download size={17} /></a>
      <button className={shared.iconButton} title="Rename or move file" aria-label={`Rename or move ${file.name}`} onClick={() => open({ kind: "file", action: "edit", file })}><Pencil size={17} /></button>
      <button className={`${shared.iconButton} ${styles.danger}`} title="Move to trash" aria-label={`Trash ${file.name}`} onClick={() => open({ kind: "file", action: "trash", file })}><Trash2 size={17} /></button>
    </> : file.state === "trashed" && file.restorable ? <button className={shared.iconButton} title="Restore file" aria-label={`Restore ${file.name}`} onClick={() => open({ kind: "file", action: "restore", file })}><RotateCcw size={17} /></button> : null}</div>;
  }
  function folderActions(folder: FolderRow) {
    return <div className={styles.actions}><button className={shared.iconButton} title="Rename folder" aria-label={`Rename ${folder.path}`} onClick={() => open({ kind: "folder", action: "rename", folder })}><Pencil size={17} /></button><button className={`${shared.iconButton} ${styles.danger}`} title="Remove empty folder" aria-label={`Remove ${folder.path}`} onClick={() => open({ kind: "folder", action: "remove", folder })}><Trash2 size={17} /></button></div>;
  }
  return <section className={`${shared.section} ${styles.section}`}>
    <div className={styles.heading}><h1>Data Administration</h1><div className={styles.actions}><button className={shared.iconButton} title="Refresh" aria-label="Refresh data" onClick={() => setRevision(value => value + 1)}><RefreshCw size={18} /></button><button className={shared.primary} onClick={() => open({ kind: "folder", action: "create" })}><FolderPlus size={17} />New folder</button></div></div>
    <DataViews view={view} onNavigate={() => { setPage(1); setLocation(null); }} />
    {view === "trash" && <RetentionSettings />}
    {notice && <div className={shared.notice} role="status"><span>{notice}</span><button className={shared.iconButton} title="Dismiss" aria-label="Dismiss notice" onClick={() => setNotice("")}><X size={16} /></button></div>}
    {location && <nav className={styles.breadcrumb} aria-label="Folder location"><button onClick={() => { setLocation(null); setPage(1); }}>All files</button><ChevronRight size={14} /><span>{location.owner_name} / {location.team_name}</span><ChevronRight size={14} />{location.path ? location.path.split("/").map((part, index, parts) => <button key={index} onClick={() => browse({ ...location, path: parts.slice(0, index + 1).join("/") })}>{part}{index < parts.length - 1 && <ChevronRight size={14} />}</button>) : <span>Drive root</span>}</nav>}
    <Filters search={search} setSearch={value => { setSearch(value); setPage(1); }} from={from} setFrom={value => { setFrom(value); setPage(1); }} to={to} setTo={value => { setTo(value); setPage(1); }} />
    <LoadState loading={state.loading} error={state.error} retry={() => setRevision(value => value + 1)} />
    {state.data && !state.data.items.length && <div className={shared.empty}>{view === "folders" ? <Folder size={30} /> : view === "trash" ? <Trash2 size={30} /> : view === "activity" ? <History size={30} /> : <File size={30} />}<h2>No {view === "activity" ? "activity" : view} found</h2>{view === "folders" && !search && !from && !to && <button className={shared.primary} onClick={() => open({ kind: "folder", action: "create" })}><FolderPlus size={17} />New folder</button>}</div>}
    {!!fileRows?.length && <><div className={styles.desktop}><table className={styles.table}><thead><tr><th>File</th><th>Employee / Team</th><th>Size / Status</th><th>{view === "trash" ? "Trashed on" : "Added on"}</th><th>Actions</th></tr></thead><tbody>{fileRows.map(file => <tr key={file.id}><td><strong>{file.name}</strong><button className={styles.path} onClick={() => browse({ ...file, path: file.folder })}><Folder size={14} />{file.folder || "Drive root"}</button>{file.purge_error && <small role="status">{file.purge_error}</small>}</td><td><strong>{file.owner_name}</strong><small>{file.team_name}</small></td><td>{bytes(file.size_bytes)}<small className={styles.badge} data-state={file.state}>{file.state === "purging" ? "Cleaning" : file.state}</small></td><td><time>{dateTime(file.trashed_at || file.created_at)}</time></td><td>{fileActions(file)}</td></tr>)}</tbody></table></div><div className={styles.mobile}>{fileRows.map(file => <article key={file.id} className={styles.item}><header><strong>{file.name}</strong><span className={styles.badge} data-state={file.state}>{file.state === "purging" ? "Cleaning" : file.state}</span></header>{file.purge_error && <p className={styles.warning} role="status">{file.purge_error}</p>}<dl><div><dt>Folder</dt><dd><button className={styles.path} onClick={() => browse({ ...file, path: file.folder })}>{file.folder || "Drive root"}</button></dd></div><div><dt>Employee</dt><dd>{file.owner_name}</dd></div><div><dt>Team</dt><dd>{file.team_name}</dd></div><div><dt>Size</dt><dd>{bytes(file.size_bytes)}</dd></div><div><dt>{view === "trash" ? "Trashed on" : "Added on"}</dt><dd>{dateTime(file.trashed_at || file.created_at)}</dd></div></dl>{fileActions(file)}</article>)}</div></>}
    {!!folders?.length && <><div className={styles.desktop}><table className={styles.table}><thead><tr><th>Folder</th><th>Employee / Team</th><th>Added on</th><th>Actions</th></tr></thead><tbody>{folders.map(folder => <tr key={`${folder.team_id}:${folder.owner_id}:${folder.path}`}><td><button className={styles.folderLink} onClick={() => browse(folder)}><Folder size={18} />{folder.path}</button></td><td><strong>{folder.owner_name}</strong><small>{folder.team_name}</small></td><td>{dateTime(folder.created_at)}</td><td>{folderActions(folder)}</td></tr>)}</tbody></table></div><div className={styles.mobile}>{folders.map(folder => <article className={styles.item} key={`${folder.team_id}:${folder.owner_id}:${folder.path}`}><button className={styles.folderLink} onClick={() => browse(folder)}><Folder size={18} />{folder.path}</button><dl><div><dt>Employee</dt><dd>{folder.owner_name}</dd></div><div><dt>Team</dt><dd>{folder.team_name}</dd></div><div><dt>Added on</dt><dd>{dateTime(folder.created_at)}</dd></div></dl>{folderActions(folder)}</article>)}</div></>}
    {!!events?.length && <ol className={styles.events}>{events.map(event => <li key={event.id}><div><strong>{actionNames[event.action] || event.action}</strong><time>{dateTime(event.created_at)}</time></div><p>{event.subject}</p><small>{event.actor}</small><p className={styles.detail}>{event.detail}</p></li>)}</ol>}
    {state.data && <Pagination total={state.data.total} page={page} setPage={setPage} />}
    <dialog ref={dialog} className={shared.dialog} aria-labelledby="data-dialog-title" onCancel={event => { if (busy) event.preventDefault(); else setEdit(null); }}>
      <form onSubmit={save}><div className={shared.dialogHeader}><h2 id="data-dialog-title">{title}</h2><button type="button" className={shared.iconButton} title="Close" aria-label="Close dialog" disabled={busy} onClick={close}><X size={18} /></button></div><fieldset className={shared.formBody} disabled={busy}>
        {edit?.kind === "file" && <p className={styles.selection}>{edit.file.name}<small>{edit.file.owner_name} / {edit.file.team_name}</small></p>}
        {edit?.kind === "file" && edit.action === "edit" && <><label className={shared.field}>File name<input required maxLength={255} value={name} onChange={event => setName(event.target.value)} /></label><label className={shared.field}>Folder path<input maxLength={255} value={path} placeholder="Drive root" onChange={event => setPath(event.target.value)} /></label></>}
        {edit?.kind === "file" && edit.action === "trash" && <p className={styles.warning}>Move this file to trash? Downloads and pending workflow access will stop. Your trash retention policy controls permanent removal; storage remains charged until cleanup completes.</p>}
        {edit?.kind === "file" && edit.action === "restore" && <p className={styles.warning}>Restore this file to {edit.file.folder || "the drive root"}?</p>}
        {edit?.kind === "folder" && edit.action === "create" && <DrivePicker selected={drive} onChange={setDrive} />}
        {edit?.kind === "folder" && edit.folder && <p className={styles.selection}>{edit.folder.path}<small>{edit.folder.owner_name} / {edit.folder.team_name}</small></p>}
        {edit?.kind === "folder" && edit.action !== "remove" && <label className={shared.field}>Folder path<input required maxLength={255} value={path} onChange={event => setPath(event.target.value)} /></label>}
        {edit?.kind === "folder" && edit.action === "remove" && <p className={styles.warning}>Remove this empty folder? Folders containing files, trash, or subfolders cannot be removed.</p>}
        {error && <p className={shared.formError} role="alert">{error}</p>}
      </fieldset><div className={shared.dialogFooter}><button type="button" className={shared.secondary} disabled={busy} onClick={close}>Cancel</button><button type="submit" className={shared.primary} disabled={busy || (edit?.kind === "folder" && !drive)}>{busy ? "Saving..." : edit?.action === "trash" ? "Move to trash" : edit?.action === "remove" ? "Remove folder" : edit?.action === "restore" ? "Restore file" : "Save"}</button></div></form>
    </dialog>
  </section>;
}