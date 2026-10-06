"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowUpRight, Files, FolderClosed, HardDrive, RefreshCw, Users } from "lucide-react";
import { useResource } from "./workflows/workflow-ui";
import styles from "./storage-dashboard.module.css";

type StorageRow = { id: string; name: string; used_bytes?: number; quota_bytes?: number; files?: number; team_name?: string; owner_name?: string; size_bytes?: number; state?: string };
type StorageSummary = { capacity_bytes: number; used_bytes: number; remaining_bytes: number; teams: number; generated_at: string; previews: Record<string, StorageRow[]> };
const widgets = [
  { key: "storage-teams", title: "Usage by team", icon: FolderClosed, empty: "No teams yet", action: "Manage teams", href: "/company/admin/teams#teams" },
  { key: "storage-employees", title: "Usage by member", icon: Users, empty: "No members yet", action: "Manage members", href: "/company/admin/teams#members" },
  { key: "largest-files", title: "Largest files", icon: Files, empty: "No files yet", action: "View company data", href: "/company/admin/data" },
];

function bytes(value: number) {
  const unit = value >= 1e12 ? "TB" : value >= 1e9 ? "GB" : value >= 1e6 ? "MB" : value >= 1e3 ? "KB" : "B";
  const divisor = { TB: 1e12, GB: 1e9, MB: 1e6, KB: 1e3, B: 1 }[unit];
  return `${(value / divisor).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${unit}`;
}

export default function StorageDashboard() {
  const [revision, setRevision] = useState(0);
  const { data, error, loading } = useResource<StorageSummary>("/api/company/insights/storage", true, revision);
  const percentage = data && data.capacity_bytes > 0 ? data.used_bytes / data.capacity_bytes * 100 : 0;
  return <section className={styles.dashboard} aria-label="Company storage" aria-busy={loading}>
    <header className={styles.heading}><h2><HardDrive size={20} />Overall storage usage</h2><button type="button" title="Refresh storage" aria-label="Refresh storage" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={17} /></button></header>
    {loading ? <div className={styles.loading} role="status" aria-label="Loading company storage"><div /><div /><div /></div> : error ? <div className={styles.error} role="alert"><p>{error}</p><button type="button" onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} />Retry</button></div> : data && <>
      <div className={styles.overall}>
        <dl className={styles.metrics}>
          <div><dt>Total used</dt><dd>{bytes(data.used_bytes)}</dd></div>
          <div><dt>Total capacity</dt><dd>{bytes(data.capacity_bytes)}</dd></div>
          <div><dt>Remaining</dt><dd>{bytes(data.remaining_bytes)}</dd></div>
        </dl>
        <div className={styles.track} role="progressbar" aria-label="Company storage used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.max(0, percentage))} aria-valuetext={`${bytes(data.used_bytes)} used of ${bytes(data.capacity_bytes)}`}><span style={{ width: `${Math.min(100, Math.max(0, percentage))}%` }} /></div>
        <div className={styles.capacity}><span>{percentage.toFixed(2)}% used{data.used_bytes > data.capacity_bytes && " · Capacity exceeded"}</span><Link href="/company/admin/team-folders">{data.teams} {data.teams === 1 ? "team" : "teams"}<ArrowUpRight size={14} /></Link></div>
      </div>
      <div className={styles.widgets}>{widgets.map(widget => {
        const rows = data.previews[widget.key] || [];
        const largest = widget.key === "largest-files";
        const maximum = Math.max(1, ...rows.map(row => row.used_bytes || 0));
        return <section className={styles.widget} key={widget.key} aria-label={widget.title}>
          <header><h3><widget.icon size={18} />{widget.title}</h3><Link href={`/company/admin/reports?view=${widget.key}`} aria-label={`View all ${widget.title.toLowerCase()}`}>View all<ArrowUpRight size={14} /></Link></header>
          {rows.length ? <ol className={styles.ranking}>{rows.map(row => <li key={row.id}>
            <div className={styles.row}><div className={styles.name}><strong title={row.name}>{row.name}</strong><span>{largest ? `${row.owner_name} · ${row.team_name}` : widget.key === "storage-teams" ? `${row.files} files · ${bytes(row.quota_bytes || 0)} allocated` : row.team_name}</span></div><strong className={styles.amount}>{bytes(largest ? row.size_bytes || 0 : row.used_bytes || 0)}</strong></div>
            {largest ? <span className={styles.fileState}>{row.state?.replaceAll("_", " ")}</span> : <div className={styles.miniTrack} aria-hidden="true"><span style={{ width: `${Math.min(100, (row.used_bytes || 0) / maximum * 100)}%` }} /></div>}
          </li>)}</ol> : <div className={styles.empty}><widget.icon size={26} /><p>{widget.empty}</p><Link href={widget.href}>{widget.action}<ArrowUpRight size={14} /></Link></div>}
        </section>;
      })}</div>
    </>}
  </section>;
}