"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight, FolderClosed, History, LoaderCircle, Pencil, Search, X } from "lucide-react";
import { teamRequest, TeamRequestError } from "@/lib/team-client";
import { notificationsChanged } from "../../../notifications";
import styles from "../../../admin/companies/companies.module.css";
import local from "./folders.module.css";

type Folder = { id: number; name: string; people: number; manager: string | null; storage_quota_bytes: number; storage_used_bytes: number; manager_can_view_drives: boolean; storage_alerts_enabled: boolean; storage_warning_percent: number; storage_critical_percent: number; created_at: string };
const endpoint = "/api/company/teams/folders";
const dateText = (value: string, full = false) => new Date(value).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC", ...(full ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false } as const : {}) });
const formatBytes = (bytes: number) => {
  const unit = bytes >= 1e12 ? "TB" : bytes >= 1e9 ? "GB" : bytes >= 1e6 ? "MB" : bytes >= 1e3 ? "KB" : "B";
  const divisor = { TB: 1e12, GB: 1e9, MB: 1e6, KB: 1e3, B: 1 }[unit];
  return `${(bytes / divisor).toLocaleString("en-GB", { maximumFractionDigits: 2 })} ${unit}`;
};

export default function FolderManager() {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const [items, setItems] = useState<Folder[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Folder | null>(null);
  const [amount, setAmount] = useState("0");
  const [unit, setUnit] = useState<"GB" | "TB">("GB");
  const [managerAccess, setManagerAccess] = useState(false);
  const [alertsEnabled, setAlertsEnabled] = useState(true);
  const [warning, setWarning] = useState("80");
  const [critical, setCritical] = useState("90");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true); setError("");
      if (from && to && from > to) { setError("Start date must not be after end date."); setLoading(false); return; }
      const params = new URLSearchParams({ page: String(page), search: query });
      if (from) params.set("from_date", from);
      if (to) params.set("to_date", to);
      try {
        const result = await teamRequest<{ items: Folder[]; total: number }>(`${endpoint}?${params}`, undefined, controller.signal);
        if (!controller.signal.aborted) {
          setItems(result.items); setTotal(result.total);
          if (page > 1 && !result.items.length) setPage(Math.max(1, Math.ceil(result.total / 10)));
        }
      } catch (failure) {
        if (!controller.signal.aborted) {
          if (failure instanceof TeamRequestError && failure.status === 401) router.replace("/company/admin/login");
          setError(failure instanceof Error ? failure.message : "Unable to load team folders.");
        }
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [page, query, from, to, revision, router]);

  function edit(folder: Folder) {
    const bytes = BigInt(folder.storage_quota_bytes);
    const fraction = String(bytes % BigInt(1e9)).padStart(9, "0").replace(/0+$/, "");
    setAmount(`${bytes / BigInt(1e9)}${fraction ? `.${fraction}` : ""}`);
    setUnit("GB"); setManagerAccess(folder.manager_can_view_drives); setEditing(folder); setFormError("");
    setAlertsEnabled(folder.storage_alerts_enabled); setWarning(String(folder.storage_warning_percent)); setCritical(String(folder.storage_critical_percent));
    dialog.current?.showModal();
  }
  function clear() { setSearch(""); setQuery(""); setFrom(""); setTo(""); setPage(1); }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || !editing) return;
    const precision = unit === "GB" ? 9 : 12;
    const match = /^(\d+)(?:\.(\d+))?$/.exec(amount);
    if (!match || (match[2]?.length || 0) > precision) { setFormError("Enter a non-negative storage amount, accurate to whole bytes."); return; }
    const bytes = BigInt(match[1]) * BigInt(10 ** precision) + BigInt((match[2] || "").padEnd(precision, "0"));
    if (bytes > BigInt(9000000000000000)) { setFormError("Maximum allocation is 9,000 TB."); return; }
    if (bytes < BigInt(editing.storage_used_bytes)) { setFormError("Allocation cannot be less than used and reserved storage."); return; }
    if (!/^\d+$/.test(warning) || !/^\d+$/.test(critical) || Number(warning) < 1 || Number(critical) > 99 || Number(warning) >= Number(critical)) { setFormError("Warning must be below critical, between 1% and 99%."); return; }
    setSaving(true); setFormError("");
    try {
      const result = await teamRequest<{ detail: string }>(`${endpoint}/${editing.id}`, { storage_quota_bytes: Number(bytes), manager_can_view_drives: managerAccess, storage_alerts_enabled: alertsEnabled, storage_warning_percent: Number(warning), storage_critical_percent: Number(critical) });
      notificationsChanged("company");
      setNotice(result.detail); dialog.current?.close(); setRevision(value => value + 1);
    } catch (failure) {
      if (failure instanceof TeamRequestError && failure.status === 401) router.replace("/company/admin/login");
      setFormError(failure instanceof Error ? failure.message : "Unable to save team folder.");
    } finally { setSaving(false); }
  }
  const filtered = Boolean(query || from || to);
  const pages = Math.max(1, Math.ceil(total / 10));
  const editButton = (folder: Folder) => <div className={local.rowActions}><Link className={styles.iconButton} href={`/company/admin/team-folders/${folder.id}/activity`} title="View activity" aria-label={`View activity for ${folder.name}`}><History size={17} /></Link><button className={styles.iconButton} onClick={() => edit(folder)} title={`Configure ${folder.name}`} aria-label={`Configure ${folder.name}`}><Pencil size={17} /></button></div>;
  const allocation = (folder: Folder) => folder.storage_quota_bytes ? formatBytes(folder.storage_quota_bytes) : "Not allocated";
  const available = (folder: Folder) => formatBytes(Math.max(0, folder.storage_quota_bytes - folder.storage_used_bytes));
  const access = (folder: Folder) => <span className={local.access} data-enabled={folder.manager_can_view_drives}>{folder.manager_can_view_drives ? "Read-only" : "Off"}</span>;

  return <section className={`${styles.section} ${local.section}`} aria-labelledby="folders-heading">
    <div className={`${styles.heading} ${local.heading}`}><h1 id="folders-heading">Team Folders</h1><span>{loading ? "Loading..." : `${total} teams`}</span></div>
    {notice && <div className={styles.notice} role="status"><span>{notice}</span><button className={styles.iconButton} onClick={() => setNotice("")} title="Dismiss" aria-label="Dismiss"><X size={17} /></button></div>}
    <div className={`${styles.filters} ${local.filters}`}>
      <form className={styles.search} onSubmit={event => { event.preventDefault(); setQuery(search.trim()); setPage(1); }}><Search size={17} /><input aria-label="Search team folders" placeholder="Search teams" value={search} maxLength={160} onChange={event => setSearch(event.target.value)} /><button className={styles.iconButton} aria-label="Search" title="Search"><ChevronRight size={18} /></button></form>
      <label className={styles.date}>Team created from<input type="date" value={from} max={to || undefined} onChange={event => { setFrom(event.target.value); setPage(1); }} /></label>
      <label className={styles.date}>Team created to<input type="date" value={to} min={from || undefined} onChange={event => { setTo(event.target.value); setPage(1); }} /></label>
      {filtered && <button className={styles.secondary} onClick={clear}>Clear</button>}
    </div>
    <div aria-live="polite" aria-busy={loading}>
      {error ? <div className={`${styles.empty} ${local.empty}`} role="alert"><p>{error}</p><button className={styles.secondary} onClick={() => setRevision(value => value + 1)}>Retry</button></div> : loading ? <div className={`${styles.skeleton} ${local.skeleton}`} role="status" aria-label="Loading team folders">{[0, 1, 2, 3].map(row => <div key={row}><span /><span /><span /></div>)}</div> : !items.length ? <div className={`${styles.empty} ${local.empty}`}><FolderClosed size={28} /><h2>{filtered ? "No matching teams" : "No teams yet"}</h2>{filtered ? <button className={styles.secondary} onClick={clear}>Clear filters</button> : <Link className={styles.primary} href="/company/admin/teams#teams">Create team</Link>}</div> : <>
        <div className={styles.tableWrap}><table className={`${styles.table} ${local.table}`}><thead><tr><th scope="col">Team</th><th scope="col">Shared allocation</th><th scope="col">Used / reserved</th><th scope="col">Available</th><th scope="col">Manager access</th><th scope="col"><span className={styles.srOnly}>Actions</span></th></tr></thead><tbody>{items.map(folder => <tr key={folder.id}>
          <td><strong>{folder.name}</strong><span className={local.secondary}>{folder.people} people</span><time className={local.secondary} dateTime={folder.created_at} title={`${dateText(folder.created_at, true)} UTC`}>Created {dateText(folder.created_at)}</time></td><td>{allocation(folder)}</td><td>{formatBytes(folder.storage_used_bytes)}</td><td>{available(folder)}</td><td>{access(folder)}<span className={local.secondary}>{folder.manager || "No manager assigned"}</span></td><td>{editButton(folder)}</td>
        </tr>)}</tbody></table></div>
        <div className={styles.mobileList}>{items.map(folder => <article key={folder.id} className={`${styles.mobileItem} ${local.mobileItem}`}><div className={styles.mobileHeading}><strong>{folder.name}</strong>{editButton(folder)}</div><dl><div><dt>Created</dt><dd><time dateTime={folder.created_at}>{dateText(folder.created_at)}</time></dd></div><div><dt>Allocation</dt><dd>{allocation(folder)}</dd></div><div><dt>Used / reserved</dt><dd>{formatBytes(folder.storage_used_bytes)}</dd></div><div><dt>Available</dt><dd>{available(folder)}</dd></div><div><dt>Manager</dt><dd>{folder.manager || "Not assigned"}</dd></div><div><dt>Drive access</dt><dd>{access(folder)}</dd></div><div><dt>People</dt><dd>{folder.people}</dd></div></dl></article>)}</div>
      </>}
    </div>
    {!loading && !error && total > 0 && <footer className={styles.pagination}><span>{(page - 1) * 10 + 1}-{Math.min(page * 10, total)} of {total} teams</span><div><button className={styles.iconButton} disabled={page <= 1} onClick={() => setPage(value => value - 1)} title="Previous page" aria-label="Previous page"><ChevronLeft size={18} /></button><span>{page} / {pages}</span><button className={styles.iconButton} disabled={page >= pages} onClick={() => setPage(value => value + 1)} title="Next page" aria-label="Next page"><ChevronRight size={18} /></button></div></footer>}
    <dialog ref={dialog} className={`${styles.dialog} ${local.dialog}`} aria-labelledby="folder-title" onCancel={event => { if (saving) event.preventDefault(); }} onClose={() => setEditing(null)}>
      <form onSubmit={save}><header className={styles.dialogHeader}><h2 id="folder-title">{editing?.name}</h2><button type="button" className={styles.iconButton} disabled={saving} onClick={() => dialog.current?.close()} title="Close" aria-label="Close"><X size={18} /></button></header>
        <fieldset className={styles.formBody} disabled={saving}>
          <div className={local.amount}><label className={styles.field}>Shared allocation<input autoFocus required inputMode="decimal" maxLength={28} value={amount} onChange={event => setAmount(event.target.value)} /></label><label className={styles.field}>Unit<select value={unit} onChange={event => setUnit(event.target.value as "GB" | "TB")}><option value="GB">GB</option><option value="TB">TB</option></select></label></div>
          <dl className={local.details}><div><dt>Used / reserved</dt><dd>{formatBytes(editing?.storage_used_bytes || 0)}</dd></div><div><dt>Member drives</dt><dd>Private</dd></div><div><dt>Manager</dt><dd>{editing?.manager || "Not assigned"}</dd></div></dl>
          <label className={local.checkbox}><input type="checkbox" checked={managerAccess} onChange={event => setManagerAccess(event.target.checked)} /><span>Manager can view all member drives (read-only)</span></label>
          <label className={local.checkbox}><input type="checkbox" checked={alertsEnabled} onChange={event => setAlertsEnabled(event.target.checked)} /><span>Storage alerts</span></label>
          <div className={local.thresholds}><label className={styles.field}>Warning (%)<input type="number" required min={1} max={98} step={1} disabled={!alertsEnabled} value={warning} onChange={event => setWarning(event.target.value)} /></label><label className={styles.field}>Critical (%)<input type="number" required min={2} max={99} step={1} disabled={!alertsEnabled} value={critical} onChange={event => setCritical(event.target.value)} /></label><label className={styles.field}>Full (%)<input value="100" readOnly disabled /></label></div>
          {formError && <p className={styles.formError} role="alert">{formError}</p>}
        </fieldset><footer className={styles.dialogFooter}><button type="button" className={styles.secondary} disabled={saving} onClick={() => dialog.current?.close()}>Cancel</button><button className={styles.primary} disabled={saving}>{saving && <LoaderCircle size={16} className={styles.spin} />}{saving ? "Saving..." : "Save changes"}</button></footer>
      </form>
    </dialog>
  </section>;
}