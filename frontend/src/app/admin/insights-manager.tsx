"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Activity, ClipboardList, Database, Download, Files, GitBranch, RefreshCw, Search, Users, Zap } from "lucide-react";
import { dateTime, Filters, LoadState, Pagination, useResource } from "../company/admin/workflows/workflow-ui";
import shared from "./companies/companies.module.css";
import data from "../company/admin/data/data.module.css";
import styles from "./insights.module.css";

type Row = Record<string, string | number | null> & { id: string; company_name: string };
type Result = { items: Row[]; total: number; generated_at: string };
type Column = [string, string];
const events: Column[] = [["created_at", "Recorded at"], ["source", "Source"], ["actor", "Actor"], ["action", "Action"], ["subject", "Subject"], ["detail", "Detail"]];
const columns: Record<string, Column[]> = {
  "storage-teams": [["name", "Team"], ["quota_bytes", "Allocated"], ["used_bytes", "Reserved storage"], ["files", "Files"], ["trash_bytes", "In trash"]],
  "storage-employees": [["name", "Employee"], ["team_name", "Drive team"], ["used_bytes", "Reserved storage"], ["files", "Files"], ["trash_bytes", "In trash"]],
  "largest-files": [["name", "File"], ["team_name", "Team"], ["owner_name", "Owner"], ["size_bytes", "Size"], ["state", "State"], ["created_at", "Created at"]],
  activity: events,
  workflows: [["name", "Workflow"], ["file_name", "File"], ["submitted_by", "Submitted by"], ["status", "Status"], ["approvals", "Approvals"], ["pending_reviews", "Pending reviews"], ["created_at", "Requested at"]],
  automation: [["name", "Workflow"], ["kind", "Job type"], ["status", "Status"], ["attempts", "Attempts"], ["created_at", "Created at"], ["updated_at", "Updated at"]],
};
const auditTabs = [{ key: "all", label: "All events", icon: ClipboardList }, { key: "data", label: "Data", icon: Database }, { key: "team_folders", label: "Team folders", icon: Users }, { key: "workflows", label: "Workflows", icon: GitBranch }];
const reportTabs = [{ key: "storage-teams", label: "Team storage", icon: Database }, { key: "storage-employees", label: "Member storage", icon: Users }, { key: "largest-files", label: "Largest files", icon: Files }, { key: "activity", label: "File & folder activity", icon: Activity }, { key: "workflows", label: "Requests & approvals", icon: GitBranch }, { key: "automation", label: "Automation outcomes", icon: Zap }];

function display(key: string, value: Row[string]) {
  if (value === null || value === "") return "Not recorded";
  if (key.endsWith("_at")) return dateTime(String(value));
  if (key.endsWith("_bytes")) {
    const bytes = Number(value);
    const unit = bytes >= 1e12 ? "TB" : bytes >= 1e9 ? "GB" : bytes >= 1e6 ? "MB" : bytes >= 1e3 ? "KB" : "B";
    const divisor = { TB: 1e12, GB: 1e9, MB: 1e6, KB: 1e3, B: 1 }[unit];
    return `${new Intl.NumberFormat("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(bytes / divisor)} ${unit}`;
  }
  return ["source", "action", "kind", "status"].includes(key) ? String(value).replaceAll("_", " ") : String(value);
}

function CompanyFilter({ selected, onChange }: { selected: { id: string; name: string } | null; onChange: (value: { id: string; name: string } | null) => void }) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const state = useResource<{ items: { id: number; name: string }[]; total: number }>(`/api/admin/companies?page=${page}&page_size=10&search=${encodeURIComponent(search)}`, "super", revision);
  return <div className={styles.companyPicker}>
    <button className={shared.secondary} type="button" aria-expanded={open} onClick={() => setOpen(!open)}>{selected?.name || "All companies"}</button>
    {open && <div className={styles.companyOptions}>
      <label>Find company<input value={search} maxLength={100} onChange={event => { setSearch(event.target.value); setPage(1); }} /></label>
      <button type="button" onClick={() => { onChange(null); setOpen(false); }}>All companies</button>
      <LoadState loading={state.loading} error={state.error} retry={() => setRevision(value => value + 1)} />
      {state.data?.items.map(company => <button key={company.id} type="button" onClick={() => { onChange({ id: String(company.id), name: company.name }); setOpen(false); }}>{company.name}</button>)}
      {state.data && <Pagination total={state.data.total} page={page} setPage={setPage} />}
    </div>}
  </div>;
}

export default function InsightsManager({ company, audit, view }: { company: boolean; audit: boolean; view: string }) {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<{ id: string; name: string } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const current = !audit && view.startsWith("storage-");
  const query = new URLSearchParams({ page: String(page), search });
  if (current) query.set("sort", "usage");
  if (!current && from) query.set("from_date", from);
  if (!current && to) query.set("to_date", to);
  if (audit) query.set("source", view);
  if (!company && selected) query.set("company_id", selected.id);
  const endpoint = `/api/${company ? "company" : "admin"}/insights/${audit ? "audit" : `reports/${view}`}`;
  const state = useResource<Result>(`${endpoint}?${query}`, company ? true : "super", revision);
  const fields: Column[] = [...(!company ? [["company_name", "Company"] as Column] : []), ...(audit ? events : columns[view])];
  const base = `${company ? "/company/admin" : "/admin"}/${audit ? "audit" : "reports"}`;
  const change = (setter: (value: string) => void) => (value: string) => { setter(value); setPage(1); setExportError(""); };

  async function exportCsv() {
    setExporting(true); setExportError("");
    try {
      const params = new URLSearchParams(query); params.set("export", "true"); params.delete("page");
      const response = await fetch(`${endpoint}?${params}`, { cache: "no-store", signal: AbortSignal.timeout(30000) });
      if (response.status === 401) { router.replace(company ? "/company/admin/login" : "/admin/login"); router.refresh(); }
      if (!response.ok) {
        const failure = await response.json();
        throw new Error(typeof failure.detail === "string" ? failure.detail : "Export failed.");
      }
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `mikan-${audit ? "audit" : view}.csv`;
      document.body.append(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setExportError(error instanceof Error ? error.message : "Export failed."); }
    finally { setExporting(false); }
  }

  return <section className={data.section}>
    <div className={data.heading}><h1>{audit ? "Audit Logs" : "Reports"}</h1><div className={styles.tools}>
      <button className={shared.iconButton} title="Refresh" aria-label="Refresh" disabled={state.loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={17} /></button>
      <button className={shared.secondary} disabled={exporting || state.loading || !state.data?.total} onClick={() => void exportCsv()}><Download size={17} />{exporting ? "Exporting..." : "Export CSV"}</button>
    </div></div>
    <nav className={data.tabs} aria-label={audit ? "Audit sources" : "Report views"}>{(audit ? auditTabs : reportTabs).map(tab => <Link key={tab.key} href={`${base}?view=${tab.key}`} scroll={false} aria-current={view === tab.key ? "page" : undefined} onNavigate={() => { setPage(1); setSearch(""); setExportError(""); }}><tab.icon size={16} />{tab.label}</Link>)}</nav>
    {!company && <CompanyFilter selected={selected} onChange={value => { setSelected(value); setPage(1); setExportError(""); }} />}
    {current ? <div className={shared.filters}><label className={shared.search}><Search size={17} /><input aria-label="Search" placeholder="Search" maxLength={100} value={search} onChange={event => change(setSearch)(event.target.value)} /></label>{search && <button className={shared.secondary} onClick={() => change(setSearch)("")}>Clear</button>}<span className={styles.note}>Current snapshot, all registered file states</span></div> : <Filters search={search} setSearch={change(setSearch)} from={from} setFrom={change(setFrom)} to={to} setTo={change(setTo)} />}
    {exportError && <p role="alert" className={shared.formError}>{exportError}</p>}
    <LoadState loading={state.loading} error={state.error} retry={() => setRevision(value => value + 1)} />
    {state.data && <>
      <div className={styles.caption}><span>{state.data.total} {audit || view === "activity" ? "recorded events" : "records"}</span><span>As of {dateTime(state.data.generated_at)}</span></div>
      {state.data.items.length ? <><div className={data.desktop} role="region" aria-label="Report records" tabIndex={0}><table className={`${data.table} ${styles.table}`}><thead><tr>{fields.map(([key, label]) => <th scope="col" key={key}>{label}</th>)}</tr></thead><tbody>{state.data.items.map(row => <tr key={row.id}>{fields.map(([key]) => <td key={key}>{key === "status" ? <span className={styles.status} data-state={row[key]}>{display(key, row[key])}</span> : display(key, row[key])}</td>)}</tr>)}</tbody></table></div>
      <div className={data.mobile}>{state.data.items.map(row => <article className={data.item} key={row.id}><dl>{fields.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{display(key, row[key])}</dd></div>)}</dl></article>)}</div></> : <div className={shared.empty}><ClipboardList size={26} /><h2>No records found</h2><p>{search || from || to || selected ? "No records match the selected filters." : "No records are available yet."}</p>{(search || from || to || selected || page > 1) && <button className={shared.secondary} onClick={() => { setSearch(""); setFrom(""); setTo(""); setSelected(null); setPage(1); }}>Reset filters</button>}</div>}
      <Pagination total={state.data.total} page={page} setPage={setPage} />
    </>}
  </section>;
}