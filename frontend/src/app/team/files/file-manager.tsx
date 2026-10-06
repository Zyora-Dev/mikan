"use client";

import { useDeferredValue, useId, useRef, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import Link from "next/link";
import { ArrowLeft, CalendarDays, ChevronRight, Download, Eye, File, FileArchive, FileImage, FileSpreadsheet, FileText, Film, Folder, FolderPlus, Info, LayoutGrid, List, ListFilter, LockKeyhole, MoreVertical, Music, RefreshCw, Search, Share2, Upload, X } from "lucide-react";
import { MAX_FILE_BYTES, teamRequest } from "@/lib/team-client";
import { getUploadQueue } from "@/lib/upload-queue";
import { outcomeLabel } from "@/lib/workflow";
import { dateTime, LoadState, Pagination, useResource } from "../../company/admin/workflows/workflow-ui";
import shared from "../../admin/companies/companies.module.css";
import styles from "../../company/admin/workflows/workflows.module.css";
import drive from "./files.module.css";
import MediaPreview, { MediaThumbnail, previewType, type PreviewFile } from "./media-preview";
import ShareDialog from "./share-dialog";
import VersionDialog from "./version-dialog";
import { History, RotateCcw, Trash2 } from "lucide-react";

type StoredFile = { kind: "file"; id: string; name: string; folder: string; size_bytes: number; state: string; created_at: string; shared_by?: string; trashed_at?: string; trashed_by_owner?: boolean; purge_error?: string };
type DriveFolder = { kind: "folder"; id: string; name: string; path: string; folder: string; created_at: string };
type Entry = StoredFile | DriveFolder;
type Listing = { items: Entry[]; total: number; drive_owner?: { id: number; name: string }; policy?: { enabled: boolean; max_file_bytes: number } | null; retention?: { enabled: boolean; retention_days: number } | null };
const bytes = (value: number) => value >= 1024 ** 4 ? `${(value / 1024 ** 4).toFixed(2)} TiB` : value >= 1073741824 ? `${(value / 1073741824).toFixed(2)} GiB` : value >= 1048576 ? `${(value / 1048576).toFixed(1)} MiB` : value >= 1024 ? `${(value / 1024).toFixed(1)} KiB` : `${value} B`;
type View = "grid" | "list" | "compact";
const views = [{ key: "grid", label: "Grid view", icon: LayoutGrid }, { key: "list", label: "List view", icon: List }, { key: "compact", label: "Compact view", icon: ListFilter }] as const;

function fileType(name: string) {
  const extension = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg", "heic"].includes(extension)) return { icon: FileImage, label: "Image", tone: "image" };
  if (["xls", "xlsx", "csv", "ods"].includes(extension)) return { icon: FileSpreadsheet, label: "Spreadsheet", tone: "sheet" };
  if (["mp4", "mov", "webm", "mkv"].includes(extension)) return { icon: Film, label: "Video", tone: "video" };
  if (["mp3", "wav", "m4a", "ogg", "flac"].includes(extension)) return { icon: Music, label: "Audio", tone: "audio" };
  if (["zip", "rar", "gz", "7z", "tar"].includes(extension)) return { icon: FileArchive, label: "Archive", tone: "archive" };
  if (["pdf", "doc", "docx", "txt", "md", "odt"].includes(extension)) return { icon: FileText, label: extension === "pdf" ? "PDF document" : "Document", tone: "document" };
  return { icon: File, label: "File", tone: "file" };
}

function ToolPopover({ label, icon, text, active, children }: { label: string; icon: ReactNode; text?: string; active?: boolean; children: ReactNode }) {
  const id = useId();
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  return <>
    <button type="button" className={text ? drive.toolButton : drive.toolIcon} popoverTarget={id} title={label} aria-label={label} data-active={active || undefined} onClick={event => {
      const bounds = event.currentTarget.getBoundingClientRect();
      setPosition({ left: Math.max(8, Math.min(bounds.left, window.innerWidth - 300)), top: Math.max(8, Math.min(bounds.bottom + 6, window.innerHeight - 240)) });
    }}>{icon}{text && <span>{text}</span>}</button>
    <div ref={panel} id={id} popover="auto" className={drive.filterPopover} style={position} aria-label={label}>{children}</div>
  </>;
}

function FileActions({ item, enabled, retry, details, preview, share, versions, cancel, trashAction, folderHref, readOnly = false }: { item: Entry; enabled: boolean; retry: () => void; details: () => void; preview?: () => void; share?: () => void; versions?: () => void; cancel?: () => void; trashAction?: () => void; folderHref?: string; readOnly?: boolean }) {
  const id = useId();
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  function close() { panel.current?.hidePopover(); }
  return <>
    <button type="button" className={drive.more} popoverTarget={id} title={`More actions for ${item.name}`} aria-label={`More actions for ${item.name}`} onClick={event => {
      const bounds = event.currentTarget.getBoundingClientRect();
      const height = panel.current?.offsetHeight || 112;
      setPosition({ left: Math.max(8, Math.min(bounds.right - 208, window.innerWidth - 216)), top: bounds.bottom + height + 8 > window.innerHeight ? Math.max(8, bounds.top - height - 6) : bounds.bottom + 6 });
    }}><MoreVertical size={18} /></button>
    <div ref={panel} id={id} popover="auto" className={drive.menu} style={position} aria-label={`Actions for ${item.name}`}>
      {item.kind === "folder" && folderHref && <Link href={folderHref} onClick={close}><Folder size={16} />Open folder</Link>}
      {preview && <button type="button" onClick={() => { close(); preview(); }}><Eye size={16} />Preview</button>}
      {share && <button type="button" onClick={() => { close(); share(); }}><Share2 size={16} />Share</button>}
      {versions && <button type="button" onClick={() => { close(); versions(); }}><History size={16} />Version history</button>}
      {trashAction && item.kind === "file" && <button type="button" onClick={() => { close(); trashAction(); }}>{item.state === "ready" ? <Trash2 size={16} /> : <RotateCcw size={16} />}{item.state === "ready" ? "Move to Trash" : "Restore file"}</button>}
      {item.kind === "file" && item.state === "ready" && <a href={`/api/team/files/${item.id}/content`} download onClick={close}><Download size={16} />Download</a>}
      {!readOnly && item.kind === "file" && item.state === "pending" && <button type="button" disabled={!enabled} onClick={() => { close(); retry(); }}><Upload size={16} />Retry upload</button>}
      {item.kind === "file" && ["pending", "cancelling"].includes(item.state) && cancel && <button type="button" onClick={() => { close(); cancel(); }}><X size={16} />{item.state === "cancelling" ? "Retry cleanup" : "Cancel upload"}</button>}
      <button type="button" onClick={() => { close(); details(); }}><Info size={16} />{item.kind === "folder" ? "Folder details" : "File details"}</button>
    </div>
  </>;
}

export default function FileManager({ view, currentFolder, owner, sharedWithMe = false, trash = false, driveOwner }: { view: View; currentFolder: string; owner: string; sharedWithMe?: boolean; trash?: boolean; driveOwner?: string }) {
  const readOnly = !!driveOwner;
  const queue = getUploadQueue(owner);
  const uploadRevision = useSyncExternalStore(queue.subscribe, queue.version, () => 0);
  const [search, setSearch] = useState("");
  const query = useDeferredValue(search);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<StoredFile | null>(null);
  const folder = selected?.folder ?? currentFolder;
  const [files, setFiles] = useState<globalThis.File[]>([]);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const detailsDialog = useRef<HTMLDialogElement>(null);
  const [detail, setDetail] = useState<Entry | null>(null);
  const [preview, setPreview] = useState<PreviewFile | null>(null);
  const [sharing, setSharing] = useState<StoredFile | null>(null);
  const [versionFile, setVersionFile] = useState<StoredFile | null>(null);
  const [cancelling, setCancelling] = useState<StoredFile | null>(null);
  const cancelDialog = useRef<HTMLDialogElement>(null);
  const [cancelError, setCancelError] = useState("");
  const [trashFiles, setTrashFiles] = useState<StoredFile[]>([]);
  const [selection, setSelection] = useState<{ scope: string; ids: string[] }>({ scope: "", ids: [] });
  const [trashError, setTrashError] = useState("");
  const trashDialog = useRef<HTMLDialogElement>(null);
  const folderDialog = useRef<HTMLDialogElement>(null);
  const [folderName, setFolderName] = useState("");
  const [folderError, setFolderError] = useState("");
  const params = new URLSearchParams({ folder: currentFolder, page: String(page), search: query, ...(driveOwner ? { owner_id: driveOwner } : {}), ...(from ? { from_date: from } : {}), ...(to ? { to_date: to } : {}) });
  const state = useResource<Listing>(`/api/team/files${trash ? "/trash" : sharedWithMe ? "/shared" : ""}?${params}`, false, revision + uploadRevision);
  const fileLimit = Math.min(state.data?.policy?.max_file_bytes ?? MAX_FILE_BYTES, MAX_FILE_BYTES);
  const oversized = files.some(file => file.size > fileLimit);
  const previewFiles = state.data?.items.filter((item): item is StoredFile => item.kind === "file" && item.state === "ready" && !!previewType(item.name)) || [];
  const previewIndex = previewFiles.findIndex(item => item.id === preview?.id);
  const segments = currentFolder ? currentFolder.split("/") : [];
  const location = (path: string, layout: View = view) => driveOwner ? `/team/drives?${new URLSearchParams({ owner: driveOwner, folder: path, view: layout })}` : `/team/files?${new URLSearchParams({ view: layout, ...(trash ? { scope: "trash" } : sharedWithMe ? { scope: "shared" } : { folder: path }) })}`;
  const selectionScope = `${owner}:${trash}:${sharedWithMe}:${params}:${search}:${revision}:${uploadRevision}`;
  const eligibleFiles = state.data?.items.filter((item): item is StoredFile => !readOnly && !sharedWithMe && item.kind === "file" && (trash ? item.state === "trashed" && !!item.trashed_by_owner : item.state === "ready")) || [];
  const selectedFiles = selection.scope === selectionScope ? eligibleFiles.filter(item => selection.ids.includes(item.id)) : [];
  const allSelected = eligibleFiles.length > 0 && selectedFiles.length === eligibleFiles.length;
  if (selection.scope !== selectionScope) setSelection({ scope: selectionScope, ids: [] });

  function confirmTrash(items: StoredFile[]) {
    if (busy || !items.length) return;
    setTrashFiles(items); setTrashError(""); trashDialog.current?.showModal();
  }

  function showDetails(item: Entry) { setDetail(item); detailsDialog.current?.showModal(); }

  async function changeTrash() {
    if (!trashFiles.length || busy) return;
    setBusy(true); setTrashError(""); setMessage("");
    const failed: StoredFile[] = [];
    const errors: string[] = [];
    let completed = 0;
    try {
      for (const item of trashFiles) {
        try {
          await teamRequest(`/api/team/files/${item.id}/${trash ? "restore" : "trash"}`, {});
          completed += 1;
        } catch (failure) {
          failed.push(item);
          errors.push(`${item.name}: ${failure instanceof Error ? failure.message : "Unable to update file."}`);
        }
      }
      if (completed) setMessage(`${completed} ${completed === 1 ? "file" : "files"} ${trash ? "restored" : "moved to Trash"}.`);
      setSelection({ scope: "", ids: [] }); setTrashFiles(failed);
      setPage(1); setRevision(value => value + 1);
      if (failed.length) setTrashError(errors.join("\n"));
      else trashDialog.current?.close();
    }
    finally { setBusy(false); }
  }

  async function cancelUpload() {
    if (!cancelling || busy) return;
    setBusy(true); setCancelError("");
    try {
      await queue.cancel(cancelling.id);
      setMessage("Upload cancelled."); cancelDialog.current?.close(); setCancelling(null);
    } catch (failure) { setCancelError(failure instanceof Error ? failure.message : "Unable to cancel upload."); }
    finally { setBusy(false); setRevision(value => value + 1); }
  }

  async function createFolder(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setFolderError(""); setMessage("");
    try {
      await teamRequest("/api/team/files/folders", { name: folderName, parent: currentFolder });
      setSearch(""); setFrom(""); setTo(""); setPage(1);
      setRevision(value => value + 1); setMessage("Folder created."); folderDialog.current?.close();
    } catch (failure) { setFolderError(failure instanceof Error ? failure.message : "Unable to create folder."); }
    finally { setBusy(false); }
  }

  function open(pending: StoredFile | null = null) {
    setSelected(pending); setFiles([]); setError(""); setDragging(false);
    if (picker.current) picker.current.value = "";
    dialog.current?.showModal();
  }
  function addFiles(incoming: globalThis.File[]) {
    if (!state.data?.policy?.enabled) return;
    setError("");
    if (selected) {
      if (incoming.length !== 1 || incoming[0].name !== selected.name || incoming[0].size !== selected.size_bytes) { setError("Select the original file with the same name and size."); return; }
      setFiles(incoming); return;
    }
    setFiles(current => {
      const next = [...current];
      for (const file of incoming) if (!next.some(existing => existing.name === file.name && existing.size === file.size && existing.lastModified === file.lastModified)) next.push(file);
      return next;
    });
  }
  function upload(event: FormEvent) {
    event.preventDefault();
    if (!files.length || !state.data?.policy?.enabled) return;
    try {
      queue.enqueue(files, folder, fileLimit, selected ?? undefined);
      setFiles([]); setMessage(""); setError(""); dialog.current?.close();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to upload file."); }
  }

  return <section className={`${styles.section} ${drive.section}`} data-team-drive={readOnly || undefined}>
    <div className={drive.heading}><h1>{readOnly ? state.data?.drive_owner ? `${state.data.drive_owner.name}'s drive` : "Team drive" : trash ? "Trash" : sharedWithMe ? "Shared with me" : "My Files"}</h1><span aria-live="polite">{state.data ? `${state.data.total} ${state.data.total === 1 ? "item" : "items"}` : ""}</span></div>
    {readOnly && <nav className={drive.scopeTabs} aria-label="Team drive"><Link href="/team#team-members"><ArrowLeft size={16} />Team members</Link><span>Read-only</span></nav>}
    {!readOnly && !trash && <nav className={drive.scopeTabs} aria-label="File location"><Link href={`/team/files?view=${view}`} aria-current={!sharedWithMe ? "page" : undefined}>My files</Link><Link href={`/team/files?scope=shared&view=${view}`} aria-current={sharedWithMe ? "page" : undefined}>Shared with me</Link></nav>}
    {trash && state.data && <p className={drive.destination}>{state.data.retention?.enabled ? `Permanent cleanup after ${state.data.retention.retention_days} days in Trash.` : "Automatic cleanup is disabled."}</p>}
    {currentFolder && <nav className={drive.breadcrumbs} aria-label="Folder location">
      {currentFolder && <Link className={drive.more} href={location(segments.slice(0, -1).join("/"))} title="Parent folder" aria-label="Parent folder"><ArrowLeft size={18} /></Link>}
      <ol><li><Link href={location("")} aria-current={!currentFolder ? "page" : undefined}>{readOnly ? "Team drive" : "My drive"}</Link></li>{segments.map((segment, index) => <li key={segments.slice(0, index + 1).join("/")}><ChevronRight size={14} aria-hidden="true" />{index === segments.length - 1 ? <span aria-current="page">{segment}</span> : <Link href={location(segments.slice(0, index + 1).join("/"))}>{segment}</Link>}</li>)}</ol>
    </nav>}
    {message && <p role="status" className={shared.notice}>{message}</p>}
    <div className={drive.toolbar} role="group" aria-label="File actions">
      <div className={drive.tools}>
        <ToolPopover label={search ? `Search: ${search}` : "Search files and folders"} icon={<Search size={17} />} active={!!search}>
          <label className={drive.filterField}>Search<input type="search" maxLength={100} value={search} placeholder="Files and folders" onChange={event => { setSearch(event.target.value); setPage(1); }} /></label>
          <button type="button" className={drive.toolButton} disabled={!search} onClick={() => { setSearch(""); setPage(1); }}>Clear search</button>
        </ToolPopover>
        <ToolPopover label={trash ? "Trashed date range" : "Created date range"} icon={<CalendarDays size={17} />} text="Date range" active={!!(from || to)}>
          <label className={drive.filterField}>{trash ? "Trashed from" : "Created from"}<input type="date" value={from} max={to || undefined} onChange={event => { setFrom(event.target.value); setPage(1); }} /></label>
          <label className={drive.filterField}>{trash ? "Trashed to" : "Created to"}<input type="date" value={to} min={from || undefined} onChange={event => { setTo(event.target.value); setPage(1); }} /></label>
          <button type="button" className={drive.toolButton} disabled={!from && !to} onClick={() => { setFrom(""); setTo(""); setPage(1); }}>Clear dates</button>
        </ToolPopover>
        {!readOnly && !sharedWithMe && !trash && <><span className={drive.divider} aria-hidden="true" />
        <button type="button" className={drive.toolButton} aria-label="New folder" title="New folder" disabled={!state.data || busy} onClick={() => { setFolderName(""); setFolderError(""); folderDialog.current?.showModal(); }}><FolderPlus size={17} /><span>New folder</span></button>
        <button type="button" className={`${drive.toolButton} ${drive.uploadButton}`} aria-label="Upload files" aria-disabled={!state.data?.policy?.enabled} disabled={!state.data || busy} title={state.data && !state.data.policy?.enabled ? "Your company admin must enable uploads in Workflow upload settings." : "Upload files"} onClick={() => open()}>{state.data && !state.data.policy?.enabled ? <LockKeyhole size={16} /> : <Upload size={17} />}<span>Upload files</span></button></>}
      </div>
      <div className={drive.tools}>
        <button type="button" className={drive.toolIcon} title="Refresh files" aria-label="Refresh files" disabled={state.loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={17} /></button>
        <nav className={drive.views} aria-label="File layout">{views.map(option => <Link key={option.key} href={location(currentFolder, option.key)} scroll={false} aria-current={view === option.key ? "page" : undefined} aria-label={option.label} title={option.label}><option.icon size={17} /></Link>)}</nav>
      </div>
    </div>
    {!readOnly && !sharedWithMe && <div className={drive.bulkActions} role="group" aria-label="File selection">
      <label><input type="checkbox" aria-label="Select all eligible files on this page" checked={allSelected} ref={element => { if (element) element.indeterminate = selectedFiles.length > 0 && !allSelected; }} disabled={busy || state.loading || !eligibleFiles.length} onChange={event => setSelection({ scope: selectionScope, ids: event.target.checked ? eligibleFiles.map(item => item.id) : [] })} />Select page</label>
      <span role="status">{selectedFiles.length} selected</span>
      <button type="button" className={drive.toolButton} disabled={busy || state.loading || !selectedFiles.length} onClick={() => confirmTrash(selectedFiles)}>{trash ? <RotateCcw size={17} /> : <Trash2 size={17} />}{trash ? "Restore selected" : "Move to Trash"}</button>
      <button type="button" className={drive.toolIcon} title="Clear selection" aria-label="Clear selection" disabled={busy || !selectedFiles.length} onClick={() => setSelection({ scope: "", ids: [] })}><X size={17} /></button>
    </div>}
    <LoadState {...state} retry={() => setRevision(value => value + 1)} />
    {state.data && <>{state.data.items.length ? <div className={drive.browser} data-view={view}>
      {view !== "grid" && <div className={drive.columns} aria-hidden="true"><span>Name</span><span>Status</span><span>File size</span><span>{trash ? "Trashed on" : "Added on"}</span><span /></div>}
      <ul className={drive.files} aria-label="Files and folders">{state.data.items.map(item => {
        if (item.kind === "folder") return <li className={drive.item} key={item.id}>
          <Link className={drive.identity} href={location(item.path)} title={item.name} aria-label={`Open folder ${item.name}`}>
            <span className={drive.artwork} data-tone="folder"><Folder size={view === "grid" ? 44 : 23} strokeWidth={1.5} /></span>
            <span className={drive.filename}><strong>{item.name}</strong>{view !== "grid" && <span className={drive.folder}>{item.folder || (readOnly ? "Team drive" : "My drive")}</span>}</span>
          </Link>
          {view !== "grid" && <><span className={drive.status}>Folder</span><span className={drive.size} /></>}
          <time className={drive.date} dateTime={item.created_at}>{dateTime(item.created_at)}</time>
          <div className={drive.actions}><FileActions item={item} enabled={false} retry={() => {}} details={() => showDetails(item)} folderHref={location(item.path)} /></div>
        </li>;
        const type = fileType(item.name);
        const media = item.state === "ready" ? previewType(item.name) : null;
        return <li className={drive.item} key={item.id} data-selected={selectedFiles.some(file => file.id === item.id) || undefined}>
          <button type="button" className={drive.identity} onClick={() => media ? setPreview(item) : showDetails(item)} title={item.name} aria-label={`${media ? "Preview" : "File details for"} ${item.name}`}>
            <span className={drive.artwork} data-tone={type.tone}>{view === "grid" && (media === "image" || media === "video") ? <MediaThumbnail key={item.id} file={item} /> : <type.icon size={view === "grid" ? 44 : 23} strokeWidth={1.5} />}</span>
            <span className={drive.filename}><strong>{item.name}</strong>{sharedWithMe ? <span className={drive.sender}>Shared by {item.shared_by}</span> : view !== "grid" && <span className={drive.folder}><Folder size={13} />{item.folder || (readOnly ? "Team drive" : "My drive")}</span>}</span>
          </button>
          {view !== "grid" && <><span className={drive.status} data-status={item.state}>{item.state === "purging" ? "Cleaning" : outcomeLabel(item.state)}</span><span className={drive.size}>{bytes(item.size_bytes)}</span></>}
          <time className={drive.date} dateTime={item.trashed_at || item.created_at}>{item.state === "purging" ? "Cleaning / " : ""}{dateTime(item.trashed_at || item.created_at)}</time>
          <div className={drive.actions}>{eligibleFiles.some(file => file.id === item.id) && <label className={drive.fileSelection}><input type="checkbox" aria-label={`Select ${item.name}`} checked={selectedFiles.some(file => file.id === item.id)} disabled={busy || state.loading} onChange={event => setSelection({ scope: selectionScope, ids: event.target.checked ? [...selectedFiles.map(file => file.id), item.id] : selectedFiles.filter(file => file.id !== item.id).map(file => file.id) })} /></label>}<FileActions item={item} readOnly={readOnly} enabled={!!state.data?.policy?.enabled} retry={() => open(item)} details={() => showDetails(item)} preview={media ? () => setPreview(item) : undefined} share={!readOnly && !sharedWithMe && item.state === "ready" ? () => setSharing(item) : undefined} versions={!readOnly && item.state === "ready" ? () => setVersionFile(item) : undefined} cancel={!readOnly && !sharedWithMe ? () => { setCancelling(item); setCancelError(""); cancelDialog.current?.showModal(); } : undefined} trashAction={eligibleFiles.some(file => file.id === item.id) ? () => confirmTrash([item]) : undefined} /></div>
        </li>;
      })}</ul>
    </div> : <div className={shared.empty}>{trash ? <Trash2 size={28} /> : <Folder size={28} />}<h2>{search || from || to ? "No matching items" : trash ? "Trash is empty" : sharedWithMe ? "No files shared with you" : currentFolder ? "This folder is empty" : readOnly ? "Team drive is empty" : "Your drive is empty"}</h2>{search || from || to ? <button className={shared.secondary} onClick={() => { setSearch(""); setFrom(""); setTo(""); setPage(1); }}>Clear filters</button> : !readOnly && state.data.policy?.enabled && <button className={shared.primary} onClick={() => open()}><Upload size={16} />Upload files</button>}</div>}<Pagination total={state.data.total} page={page} setPage={setPage} /></>}
    {sharing && <ShareDialog key={sharing.id} file={sharing} onClose={() => setSharing(null)} />}
    {versionFile && <VersionDialog key={versionFile.id} file={versionFile} onClose={() => setVersionFile(null)} onChanged={() => setRevision(value => value + 1)} />}
    {preview && <MediaPreview key={preview.id} file={preview} previous={previewIndex > 0 ? previewFiles[previewIndex - 1] : undefined} next={previewIndex >= 0 ? previewFiles[previewIndex + 1] : undefined} onNavigate={setPreview} onClose={() => setPreview(null)} />}
    <dialog ref={detailsDialog} className={shared.dialog} aria-labelledby="file-detail-title">
      <header className={shared.dialogHeader}><h2 id="file-detail-title">{detail?.kind === "folder" ? "Folder details" : "File details"}</h2><button type="button" className={shared.iconButton} title="Close details" aria-label="Close details" onClick={() => detailsDialog.current?.close()}><X size={18} /></button></header>
      {detail?.kind === "file" && detail.purge_error && <p className={shared.formError} role="status">{detail.purge_error}</p>}
      {detail?.kind === "file" && detail.state === "trashed" && !detail.trashed_by_owner && <p className={drive.destination}>Restoration is managed by your company administrator.</p>}
      {detail && <dl className={drive.details}><div><dt>Name</dt><dd>{detail.name}</dd></div><div><dt>Type</dt><dd>{detail.kind === "folder" ? "Folder" : fileType(detail.name).label}</dd></div><div><dt>{sharedWithMe ? "Shared by" : "Location"}</dt><dd>{sharedWithMe && detail.kind === "file" ? detail.shared_by : detail.folder || (readOnly ? "Team drive" : "My drive")}</dd></div>{detail.kind === "file" && <><div><dt>Size</dt><dd>{bytes(detail.size_bytes)}</dd></div><div><dt>Status</dt><dd>{outcomeLabel(detail.state)}</dd></div></>}<div><dt>Created</dt><dd>{dateTime(detail.created_at)}</dd></div></dl>}
      <footer className={shared.dialogFooter}><button type="button" className={shared.secondary} onClick={() => detailsDialog.current?.close()}>Close</button>{detail?.kind === "file" && detail.state === "ready" && <a className={shared.primary} href={`/api/team/files/${detail.id}/content`} download><Download size={16} />Download</a>}</footer>
    </dialog>
    <dialog ref={folderDialog} className={shared.dialog} aria-labelledby="folder-title" onCancel={event => { if (busy) event.preventDefault(); }}>
      <form onSubmit={event => void createFolder(event)}>
        <header className={shared.dialogHeader}><h2 id="folder-title">New folder</h2><button type="button" className={shared.iconButton} disabled={busy} title="Close" aria-label="Close" onClick={() => folderDialog.current?.close()}><X size={18} /></button></header>
        <div className={styles.uploadBody}>{folderError && <p role="alert" className={shared.formError}>{folderError}</p>}<label className={styles.field}>Folder name<input required maxLength={Math.max(1, 255 - (currentFolder ? currentFolder.length + 1 : 0))} value={folderName} disabled={busy} onChange={event => setFolderName(event.target.value)} /></label><p className={drive.destination}>{currentFolder || "My drive"}</p></div>
        <footer className={shared.dialogFooter}><button type="button" className={shared.secondary} disabled={busy} onClick={() => folderDialog.current?.close()}>Cancel</button><button className={shared.primary} disabled={busy || !folderName.trim()}><FolderPlus size={16} />{busy ? "Creating..." : "Create folder"}</button></footer>
      </form>
    </dialog>
    <dialog ref={trashDialog} className={shared.dialog} aria-labelledby="trash-file-title" onCancel={event => { if (busy) event.preventDefault(); }}>
      <header className={shared.dialogHeader}><h2 id="trash-file-title">{trash ? "Restore files?" : "Move to Trash?"}</h2><button type="button" className={shared.iconButton} title="Close" aria-label="Close" disabled={busy} onClick={() => trashDialog.current?.close()}><X size={18} /></button></header>
      <div className={drive.uploadBody}><ul className={drive.bulkFileList}>{trashFiles.map(item => <li key={item.id}>{item.name}{trash && <span> / {item.folder || "My drive"}</span>}</li>)}</ul><p>{trash ? "Files return to their original folders. Existing sharing access will resume." : "This is not an immediate permanent deletion. Access stops, and files remain in Trash until your company's retention policy removes them. Storage stays charged until cleanup completes."}</p>{trashError && <p role="alert" className={`${shared.formError} ${drive.bulkError}`}>{trashError}</p>}</div>
      <footer className={shared.dialogFooter}><button type="button" className={shared.secondary} disabled={busy} onClick={() => trashDialog.current?.close()}>Cancel</button><button type="button" className={shared.primary} disabled={busy || !trashFiles.length} onClick={() => void changeTrash()}>{trash ? <RotateCcw size={16} /> : <Trash2 size={16} />}{busy ? "Saving..." : trashError ? "Retry failed files" : trash ? "Restore files" : "Move to Trash"}</button></footer>
    </dialog>
    <dialog ref={cancelDialog} className={shared.dialog} aria-labelledby="cancel-upload-title" onCancel={event => { if (busy) event.preventDefault(); }}>
      <header className={shared.dialogHeader}><h2 id="cancel-upload-title">Cancel upload?</h2><button type="button" className={shared.iconButton} title="Close" aria-label="Close cancellation" disabled={busy} onClick={() => cancelDialog.current?.close()}><X size={18} /></button></header>
      <div className={drive.uploadBody}><p>{cancelling?.name}</p><p>Uploaded parts will be removed. Reserved storage is released after cleanup is confirmed.</p>{cancelError && <p role="alert" className={shared.formError}>{cancelError}</p>}</div>
      <footer className={shared.dialogFooter}><button type="button" className={shared.secondary} disabled={busy} onClick={() => cancelDialog.current?.close()}>Keep upload</button><button type="button" className={shared.primary} disabled={busy} onClick={() => void cancelUpload()}><X size={16} />{busy ? "Cleaning up..." : "Cancel upload"}</button></footer>
    </dialog>
    <dialog ref={dialog} className={`${shared.dialog} ${drive.uploadDialog}`} aria-labelledby="upload-title">
      <form onSubmit={upload}>
        <header className={`${shared.dialogHeader} ${drive.uploadHeader}`}><div><Upload size={22} /><h2 id="upload-title">{selected ? "Retry upload" : "Upload files"}</h2></div><button type="button" className={shared.iconButton} title="Close" aria-label="Close" onClick={() => dialog.current?.close()}><X size={18} /></button></header>
        <div className={drive.uploadBody}>
          {!state.data?.policy?.enabled && <p className={shared.formError} role="status">Your company admin must enable uploads in Workflows &gt; Upload settings.</p>}
          {error && <p className={shared.formError} role="alert">{error}</p>}
          <fieldset className={drive.uploadFields} disabled={!state.data?.policy?.enabled}>
            <p className={drive.uploadDestination}><Folder size={16} aria-hidden="true" /><span>My drive{folder ? ` / ${folder}` : ""}</span></p>
            {selected && <p className={drive.originalFile}>Original file: <strong>{selected.name}</strong> / {bytes(selected.size_bytes)}</p>}
            <div className={drive.dropzone} data-dragging={dragging || undefined} data-disabled={!state.data?.policy?.enabled || undefined}
              onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = state.data?.policy?.enabled ? "copy" : "none"; if (state.data?.policy?.enabled) setDragging(true); }}
              onDragLeave={event => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
              onDrop={event => {
                event.preventDefault(); setDragging(false);
                if (!state.data?.policy?.enabled) return;
                if (Array.from(event.dataTransfer.items).some(item => item.webkitGetAsEntry?.()?.isDirectory)) { setError("Choose individual files, not folders."); return; }
                addFiles(Array.from(event.dataTransfer.files));
              }}>
              <Upload size={32} strokeWidth={1.5} aria-hidden="true" />
              <strong>{selected ? "Original file" : "Files to upload"}</strong>
              <button type="button" className={shared.secondary} onClick={() => picker.current?.click()}><File size={16} />{selected ? "Choose file" : "Choose files"}</button>
              <input ref={picker} type="file" multiple={!selected} hidden aria-label={selected ? "Choose original file" : "Choose files to upload"} onChange={event => { addFiles(Array.from(event.target.files || [])); event.target.value = ""; }} />
              <p className={drive.fileLimit}>Maximum single file size: <strong>4 TB</strong></p>
              {fileLimit < MAX_FILE_BYTES && <p className={drive.companyLimit}>Your company allows up to {bytes(fileLimit)} per file.</p>}
            </div>
            {files.length > 0 && <div>
              <div className={drive.selectionSummary}><span role="status">{files.length} {files.length === 1 ? "file" : "files"} selected / {bytes(files.reduce((total, file) => total + file.size, 0))}</span><button type="button" className={drive.toolButton} onClick={() => setFiles([])}>Clear all</button></div>
              <ul className={drive.selectedFiles}>{files.map((file, index) => {
                const type = fileType(file.name);
                return <li key={JSON.stringify([file.name, file.size, file.lastModified])}><span className={drive.artwork} data-tone={type.tone}><type.icon size={21} /></span><div><strong title={file.name}>{file.name}</strong><span>{bytes(file.size)}</span>{file.size > fileLimit && <p className={drive.queueError}>Exceeds the single file maximum of {fileLimit === MAX_FILE_BYTES ? "4 TB" : bytes(fileLimit)}.</p>}</div><button type="button" className={drive.toolIcon} title={`Remove ${file.name}`} aria-label={`Remove ${file.name}`} onClick={() => setFiles(current => current.filter((_, position) => position !== index))}><X size={16} /></button></li>;
              })}</ul>
            </div>}
          </fieldset>
        </div>
        <footer className={shared.dialogFooter}><button type="button" className={shared.secondary} onClick={() => dialog.current?.close()}>Cancel</button><button className={shared.primary} disabled={!files.length || oversized || !state.data?.policy?.enabled}><Upload size={16} />{selected ? "Retry upload" : files.length ? `Upload ${files.length} ${files.length === 1 ? "file" : "files"}` : "Upload files"}</button></footer>
      </form>
    </dialog>
  </section>;
}