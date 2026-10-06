"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Bell, CheckCheck, CircleAlert, GitBranch, Mail, MailOpen, RefreshCw } from "lucide-react";
import { teamRequest, TeamRequestError } from "@/lib/team-client";
import { dateTime, Filters, LoadState, Pagination, useResource } from "./company/admin/workflows/workflow-ui";
import shared from "./admin/companies/companies.module.css";
import styles from "./notifications.module.css";

export type NotificationScope = "admin" | "company" | "team";
export type NotificationView = "all" | "unread" | "read";
const home = (scope: NotificationScope) => scope === "company" ? "/company/admin/notifications" : `/${scope}/notifications`;
const authScope = (scope: NotificationScope) => scope === "admin" ? "super" : scope === "company";
const login = (scope: NotificationScope) => scope === "admin" ? "/admin/login" : scope === "company" ? "/company/admin/login" : "/";
export function notificationsChanged(scope: NotificationScope) { window.dispatchEvent(new CustomEvent("mikan-notifications", { detail: scope })); }

function useNotificationRevision(scope: NotificationScope) {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const changed = (event: Event) => {
      if ((event as CustomEvent).detail !== scope) return;
      clearTimeout(timer);
      timer = setTimeout(() => setRevision(value => value + 1), 150);
    };
    window.addEventListener("mikan-notifications", changed);
    return () => { clearTimeout(timer); window.removeEventListener("mikan-notifications", changed); };
  }, [scope]);
  return revision;
}

export function NotificationBell({ scope }: { scope: NotificationScope }) {
  const pathname = usePathname();
  const router = useRouter();
  const revision = useNotificationRevision(scope);
  const state = useResource<{ unread: number }>(`/api/${scope}/notifications/unread?route=${encodeURIComponent(pathname)}`, authScope(scope), revision);
  useEffect(() => {
    let source: EventSource | undefined;
    const refresh = () => { if (document.visibilityState === "visible") notificationsChanged(scope); };
    const visibility = () => {
      if (document.visibilityState !== "visible") { source?.close(); source = undefined; return; }
      if (!source && typeof EventSource !== "undefined") {
        source = new EventSource(`/api/${scope}/notifications/stream`);
        source.addEventListener("changed", refresh);
        source.addEventListener("expired", () => { source?.close(); router.replace(login(scope)); router.refresh(); });
        source.addEventListener("unavailable", refresh);
        source.onerror = refresh;
      }
      refresh();
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", visibility);
    const timer = window.setInterval(() => { if (!source || source.readyState !== EventSource.OPEN) refresh(); }, 60000);
    visibility();
    return () => { source?.close(); window.clearInterval(timer); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", visibility); };
  }, [scope, router]);
  const count = state.data?.unread;
  const label = count === undefined ? state.error ? "Notifications: count unavailable" : "Notifications" : `Notifications: ${count} unread`;
  return <Link href={home(scope)} className={styles.bell} title={label} aria-label={label} aria-current={pathname === home(scope) ? "page" : undefined}><Bell size={21} />{count !== undefined && count > 0 && <span className={styles.badge} aria-hidden="true">{count > 99 ? "99+" : count}</span>}{state.error && <span className={styles.badge} aria-hidden="true">!</span>}</Link>;
}

type Notice = { id: string; message: string; kind: "workflow" | "automation_failure" | "storage_warning" | "storage_critical" | "storage_full"; read_at: string | null; created_at: string; run_id: string | null };
type Inbox = { items: Notice[]; total: number; unread: number; before: string };
const kinds = { workflow: "Workflow", automation_failure: "Automation failed", storage_warning: "Storage warning", storage_critical: "Storage critical", storage_full: "Storage full" };

export default function NotificationCenter({ scope, view }: { scope: NotificationScope; view: NotificationView }) {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [kind, setKind] = useState("all");
  const [page, setPage] = useState(1);
  const revision = useNotificationRevision(scope);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const params = new URLSearchParams({ page: String(page), search, view, kind });
  if (from) params.set("from_date", from);
  if (to) params.set("to_date", to);
  const endpoint = `/api/${scope}/notifications`;
  const state = useResource<Inbox>(`${endpoint}?${params}`, authScope(scope), revision);
  const data = state.data;
  const filter = (setter: (value: string) => void) => (value: string) => { setter(value); setPage(1); };
  function refresh() { notificationsChanged(scope); }
  async function mutate(path: string, payload: object) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await teamRequest<{ detail: string }>(`${endpoint}/${path}`, payload);
      setMessage(result.detail); refresh();
      if (data?.items.length === 1 && view !== "all" && page > 1) setPage(value => value - 1);
    } catch (failure) {
      if (failure instanceof TeamRequestError && failure.status === 401) { router.replace(login(scope)); router.refresh(); }
      setError(failure instanceof Error ? failure.message : "Unable to update notification.");
    } finally { setBusy(false); }
  }
  return <section className={styles.section} aria-labelledby="notifications-heading">
    <div className={styles.heading}><h1 id="notifications-heading">Notifications</h1><div className={styles.actions}><button className={shared.iconButton} type="button" title="Refresh notifications" aria-label="Refresh notifications" disabled={busy || state.loading} onClick={refresh}><RefreshCw size={18} /></button><button className={shared.secondary} type="button" disabled={busy || !data?.unread} onClick={() => data && void mutate("read-all", { before: data.before })}><CheckCheck size={17} />Mark all read</button></div></div>
    <nav className={styles.tabs} aria-label="Notification views">{(["all", "unread", "read"] as const).map(tab => <Link href={`${home(scope)}?view=${tab}`} key={tab} aria-current={tab === view ? "page" : undefined}>{tab === "all" ? "All" : tab === "unread" ? "Unread" : "Read"}</Link>)}</nav>
    <Filters search={search} setSearch={filter(setSearch)} from={from} setFrom={filter(setFrom)} to={to} setTo={filter(setTo)} />
    {scope !== "admin" && <label className={styles.category}>Category<select value={kind} onChange={event => filter(setKind)(event.target.value)}><option value="all">All notifications</option><option value="storage">Storage</option><option value="workflow">Workflows</option><option value="automation_failure">Automation failures</option></select></label>}
    {error && <p className={shared.formError} role="alert">{error}</p>}
    <p className={styles.status} role="status">{message}</p>
    <LoadState loading={state.loading} error={state.error} retry={refresh} />
    {data && <><div className={styles.summary}>{data.unread} unread</div>{!data.items.length ? <div className={shared.empty}><Bell size={26} /><h2>No {view === "all" ? "" : `${view} `}notifications</h2>{(search || from || to || kind !== "all") && <button className={shared.secondary} onClick={() => { setSearch(""); setFrom(""); setTo(""); setKind("all"); setPage(1); }}>Clear filters</button>}</div> : <ul className={styles.list}>{data.items.map(notice => <li key={notice.id} data-unread={!notice.read_at}>
      <span className={styles.kindIcon}>{notice.kind === "workflow" ? <GitBranch size={19} /> : <CircleAlert size={19} />}</span>
      <div className={styles.content}><div className={styles.meta}><strong data-kind={notice.kind}>{kinds[notice.kind]}</strong><span>{notice.read_at ? "Read" : "Unread"}</span><time dateTime={notice.created_at}>{dateTime(notice.created_at)}</time></div><p>{notice.message}</p>{notice.kind === "automation_failure" && scope === "company" ? <Link href="/company/admin/workflows?view=jobs">View automation log</Link> : notice.run_id && scope !== "admin" && <Link href={`${scope === "company" ? "/company/admin" : "/team"}/workflows/runs/${notice.run_id}`}>View request</Link>}</div>
      <button type="button" className={shared.iconButton} disabled={busy} title={notice.read_at ? "Mark unread" : "Mark read"} aria-label={`${notice.read_at ? "Mark unread" : "Mark read"}: ${notice.message}`} onClick={() => void mutate(`${notice.id}/read`, { read: !notice.read_at })}>{notice.read_at ? <Mail size={18} /> : <MailOpen size={18} />}</button>
    </li>)}</ul>}<Pagination total={data.total} page={page} setPage={setPage} /></>}
  </section>;
}