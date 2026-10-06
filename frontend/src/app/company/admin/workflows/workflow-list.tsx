"use client";

import Link from "next/link";
import { useDeferredValue, useState } from "react";
import { ArrowRight, Bell, Check, GitBranch, Plus, RefreshCw, RotateCw } from "lucide-react";
import { teamRequest } from "@/lib/team-client";
import { outcomeLabel } from "@/lib/workflow";
import { dateTime, Filters, LoadState, Pagination, useResource } from "./workflow-ui";
import shared from "@/app/admin/companies/companies.module.css";
import styles from "./workflows.module.css";
import UploadSettings from "./upload-settings";

export type WorkflowView = "definitions" | "runs" | "jobs" | "uploads" | "available" | "mine" | "inbox" | "notifications";
type Item = { id: string | number; name?: string; enabled?: boolean; version?: number; workflow_name?: string; file_name?: string; status?: string; message?: string; read_at?: string | null; created_at: string; kind?: string; attempts?: number; last_error?: string; run_id?: string; next_at?: string };
export default function WorkflowList({ company, view }: { company: boolean; view: WorkflowView }) {
  const [search, setSearch] = useState("");
  const query = useDeferredValue(search);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState<string | number | null>(null);
  const [error, setError] = useState("");
  const base = company ? "/company/admin/workflows" : "/team/workflows";
  const api = company ? "/api/company/teams/workflows" : "/api/team/workflows";
  const definition = view === "definitions" || view === "available";
  const endpoint = definition || view === "uploads" ? api : view === "jobs" ? `${api}/jobs` : view === "notifications" ? `${api}/notifications` : `${api}/runs`;
  const params = new URLSearchParams({ page: String(page), search: query, ...(from ? { from_date: from } : {}), ...(to ? { to_date: to } : {}), ...(!company && !definition && view !== "notifications" ? { view } : {}) });
  const state = useResource<{ items: Item[]; total: number }>(`${endpoint}?${params}`, company, revision);
  const tabs: [WorkflowView, string][] = company ? [["definitions", "Workflows"], ["runs", "Requests"], ["jobs", "Automation log"], ["uploads", "Upload settings"]] : [["available", "Available workflows"], ["mine", "My requests"], ["inbox", "Assigned to me"], ["notifications", "Notifications"]];
  async function markRead(identifier: Item["id"]) {
    if (busy !== null) return;
    setBusy(identifier); setError("");
    try { await teamRequest(`${api}/notifications/${identifier}/read`, {}); setRevision(value => value + 1); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to update notification."); }
    finally { setBusy(null); }
  }
  async function retryJob(identifier: Item["id"]) {
    if (busy !== null || !window.confirm("Retry this automation? External delivery may have succeeded before an earlier timeout.")) return;
    setBusy(identifier); setError("");
    try { await teamRequest(`${api}/jobs/${identifier}/retry`, {}); setRevision(value => value + 1); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to retry automation."); }
    finally { setBusy(null); }
  }
  return <section className={styles.section}>
    <div className={styles.heading}><h1>Workflows</h1>{company && <Link className={shared.primary} href={`${base}/new`}><Plus size={17} />Create workflow</Link>}</div>
    <nav className={styles.tabs} aria-label="Workflow views">{tabs.map(([key, label]) => <Link key={key} href={`${base}?view=${key}`} aria-current={view === key ? "page" : undefined}>{label}</Link>)}</nav>
    {view === "uploads" ? <UploadSettings /> : <>
    {view === "jobs" && <div className={styles.actions}><button type="button" className={shared.secondary} disabled={state.loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} />Refresh</button></div>}
    <Filters search={search} setSearch={value => { setSearch(value); setPage(1); }} from={from} setFrom={value => { setFrom(value); setPage(1); }} to={to} setTo={value => { setTo(value); setPage(1); }} />
    {error && <p className={shared.formError} role="alert">{error}</p>}
    <LoadState {...state} retry={() => setRevision(value => value + 1)} />
    {state.data && <>{!state.data.items.length ? <div className={shared.empty}>{view === "notifications" ? <Bell size={28} /> : <GitBranch size={28} />}<h2>{definition ? "No matching workflows" : view === "notifications" ? "No notifications" : "No matching requests"}</h2>{company && definition && <Link href={`${base}/new`} className={shared.primary}><Plus size={16} />Create workflow</Link>}</div> : <ul className={styles.list}>{state.data.items.map(item => {
      const href = definition ? company ? `${base}/${item.id}` : `${base}/submit/${item.id}` : `${base}/runs/${view === "jobs" ? item.run_id : item.id}`;
      const status = definition ? company ? item.enabled ? "published" : "draft" : "available" : item.status || (item.read_at ? "read" : "unread");
      return <li className={styles.row} key={item.id}>
        <div>{view === "jobs" ? <><strong>{item.workflow_name}</strong><small>{outcomeLabel(item.kind || "")} / {item.attempts} attempts</small>{item.last_error && <small role="status">{item.last_error}</small>}{item.status === "retry" && item.next_at && <small>Next attempt: {dateTime(item.next_at)}</small>}</> : <>{view === "notifications" ? <strong>{item.message}</strong> : <Link href={href}><strong>{item.name || item.file_name}</strong></Link>}<small>{item.workflow_name || (item.version ? `Version ${item.version}` : "")}</small></>}</div>
        <span className={styles.badge} data-status={status}>{outcomeLabel(status)}</span><time dateTime={item.created_at}>{dateTime(item.created_at)}</time>
        {view === "jobs" && (item.status === "failed" || item.status === "retry") ? <button className={shared.iconButton} title="Retry automation" aria-label="Retry automation" disabled={busy !== null} onClick={() => void retryJob(item.id)}><RotateCw size={17} /></button> : view === "jobs" && !item.run_id ? <span /> : view === "notifications" ? !item.read_at ? <button className={shared.iconButton} title="Mark read" aria-label="Mark notification read" disabled={busy !== null} onClick={() => void markRead(item.id)}><Check size={17} /></button> : <Check size={17} aria-label="Read" /> : <Link href={href} className={shared.iconButton} title={definition ? company ? "Edit workflow" : "Submit a file" : "View request"} aria-label={definition ? company ? "Edit workflow" : "Submit a file" : "View request"}><ArrowRight size={18} /></Link>}
      </li>;
    })}</ul>}<Pagination total={state.data.total} page={page} setPage={setPage} /></>}
    </>}
  </section>;
}