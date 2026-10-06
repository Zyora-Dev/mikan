"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ChevronLeft, ChevronRight, History } from "lucide-react";
import { teamRequest, TeamRequestError } from "@/lib/team-client";
import styles from "../../../admin/companies/companies.module.css";
import local from "./folders.module.css";

type Activity = { id: number; kind: "created" | "tracking_started" | "settings_changed"; actor_name: string | null; previous_quota_bytes: number | null; quota_bytes: number | null; previous_manager_access: boolean | null; manager_access: boolean | null; created_at: string };
type ActivityList = { team: { id: number; name: string; created_at: string }; items: Activity[]; total: number };
const dateText = (value: string) => new Date(value).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "UTC" });
const formatBytes = (bytes: number) => {
  const unit = bytes >= 1e12 ? "TB" : bytes >= 1e9 ? "GB" : bytes >= 1e6 ? "MB" : bytes >= 1e3 ? "KB" : "B";
  const divisor = { TB: 1e12, GB: 1e9, MB: 1e6, KB: 1e3, B: 1 }[unit];
  return `${(bytes / divisor).toLocaleString("en-GB", { maximumFractionDigits: 2 })} ${unit}`;
};

export default function FolderActivity({ teamId }: { teamId: string }) {
  const router = useRouter();
  const [activity, setActivity] = useState<ActivityList | null>(null);
  const [page, setPage] = useState(1);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [missing, setMissing] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true); setError(""); setMissing(false);
      if (from && to && from > to) { setError("Start date must not be after end date."); setLoading(false); return; }
      const params = new URLSearchParams({ page: String(page) });
      if (from) params.set("from_date", from);
      if (to) params.set("to_date", to);
      try {
        const result = await teamRequest<ActivityList>(`/api/company/teams/folders/${teamId}/activity?${params}`, undefined, controller.signal);
        if (!controller.signal.aborted) {
          setActivity(result);
          if (page > 1 && !result.items.length) setPage(Math.max(1, Math.ceil(result.total / 10)));
        }
      } catch (failure) {
        if (!controller.signal.aborted) {
          if (failure instanceof TeamRequestError && failure.status === 401) router.replace("/company/admin/login");
          if (failure instanceof TeamRequestError && failure.status === 404) { setMissing(true); setActivity(null); }
          setError(failure instanceof Error ? failure.message : "Unable to load activity.");
        }
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [teamId, page, from, to, revision, router]);

  function clear() { setFrom(""); setTo(""); setPage(1); }
  const pages = Math.max(1, Math.ceil((activity?.total || 0) / 10));

  return <section className={`${styles.section} ${local.section}`} aria-labelledby="activity-title">
    <Link href="/company/admin/team-folders" className={local.backLink}><ArrowLeft size={17} />Team Folders</Link>
    <div className={`${styles.heading} ${local.heading}`}><div className={local.activityTitle}><h1 id="activity-title">{activity ? `${activity.team.name} activity` : "Team Folder Activity"}</h1>{activity && <p className={local.secondary}>Created <time dateTime={activity.team.created_at}>{dateText(activity.team.created_at)} UTC</time></p>}</div><span>{loading ? "Loading..." : activity ? `${activity.total} events` : ""}</span></div>
    <div className={`${styles.filters} ${local.filters}`}>
      <label className={styles.date}>Activity from (UTC)<input type="date" value={from} max={to || undefined} onChange={event => { setFrom(event.target.value); setPage(1); }} /></label>
      <label className={styles.date}>Activity to (UTC)<input type="date" value={to} min={from || undefined} onChange={event => { setTo(event.target.value); setPage(1); }} /></label>
      {(from || to) && <button className={styles.secondary} onClick={clear}>Clear</button>}
    </div>
    <div aria-live="polite" aria-busy={loading}>
      {error ? <div className={`${styles.empty} ${local.empty}`} role="alert"><p>{error}</p>{!missing && <button className={styles.secondary} onClick={() => setRevision(value => value + 1)}>Retry</button>}</div> : loading ? <div className={`${styles.skeleton} ${local.skeleton}`} role="status" aria-label="Loading folder activity">{[0, 1, 2, 3].map(row => <div key={row}><span /><span /><span /></div>)}</div> : !activity?.items.length ? <div className={`${styles.empty} ${local.empty}`}><History size={26} /><p>No activity in this period.</p>{(from || to) && <button className={styles.secondary} onClick={clear}>Clear filters</button>}</div> : <ol className={local.activityList}>{activity.items.map(event => <li key={event.id}>
        <div className={local.eventHeading}><strong>{event.kind === "created" ? "Team folder created" : event.kind === "tracking_started" ? "Activity tracking started" : "Folder settings changed"}</strong><time dateTime={event.created_at}>{dateText(event.created_at)} UTC</time></div>
        <p className={local.secondary}>{event.actor_name ? `By ${event.actor_name}` : event.kind === "created" ? "Creator not recorded; date from team record." : "Existing settings recorded; earlier changes are unavailable."}</p>
        {event.quota_bytes !== null && (event.kind !== "settings_changed" || event.previous_quota_bytes !== event.quota_bytes) && <p className={local.eventDetail}><strong>Allocation:</strong> {event.previous_quota_bytes !== null && <><span title={`${event.previous_quota_bytes.toLocaleString("en-GB")} bytes`}>{formatBytes(event.previous_quota_bytes)}</span> to </>}<span title={`${event.quota_bytes.toLocaleString("en-GB")} bytes`}>{formatBytes(event.quota_bytes)}</span></p>}
        {event.manager_access !== null && (event.kind !== "settings_changed" || event.previous_manager_access !== event.manager_access) && <p className={local.eventDetail}><strong>Manager drive access:</strong> {event.previous_manager_access !== null && `${event.previous_manager_access ? "Read-only" : "Off"} to `}{event.manager_access ? "Read-only" : "Off"}</p>}
      </li>)}</ol>}
    </div>
    {!loading && !error && activity && activity.total > 0 && <footer className={styles.pagination}><span>{(page - 1) * 10 + 1}-{Math.min(page * 10, activity.total)} of {activity.total} events</span><div><button className={styles.iconButton} disabled={page <= 1} onClick={() => setPage(value => value - 1)} title="Previous activity page" aria-label="Previous activity page"><ChevronLeft size={18} /></button><span>{page} / {pages}</span><button className={styles.iconButton} disabled={page >= pages} onClick={() => setPage(value => value + 1)} title="Next activity page" aria-label="Next activity page"><ChevronRight size={18} /></button></div></footer>}
  </section>;
}