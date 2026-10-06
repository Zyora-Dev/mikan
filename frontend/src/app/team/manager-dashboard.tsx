"use client";

import Link from "next/link";
import { useState, useSyncExternalStore } from "react";
import { ArrowUpRight, ClipboardCheck, Files, FolderOpen, HardDrive, LockKeyhole, RefreshCw, Users } from "lucide-react";
import { getUploadQueue } from "@/lib/upload-queue";
import { dateTime, LoadState, useResource } from "../company/admin/workflows/workflow-ui";
import styles from "./manager.module.css";

type TeamFile = { id: string; name: string; folder: string; owner_id: number; owner_name: string; size_bytes: number; created_at: string };
type Summary = { name: string; used_bytes: number; quota_bytes: number; remaining_bytes: number; members: number; files: number; pending_reviews: number; manager_can_view_drives: boolean; recent_files: TeamFile[]; largest_files: TeamFile[] };
type Review = { id: string; workflow_name: string; file_name: string; created_at: string };

export function storageSize(value: number) {
  const unit = value >= 1e12 ? "TB" : value >= 1e9 ? "GB" : value >= 1e6 ? "MB" : value >= 1e3 ? "KB" : "B";
  return `${(value / { TB: 1e12, GB: 1e9, MB: 1e6, KB: 1e3, B: 1 }[unit]).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${unit}`;
}

export default function ManagerDashboard({ owner, revision: externalRevision }: { owner: string; revision: number }) {
  const [revision, setRevision] = useState(0);
  const queue = getUploadQueue(owner);
  const uploadRevision = useSyncExternalStore(queue.subscribe, queue.version, () => 0);
  const state = useResource<Summary>("/api/team/dashboard", false, revision + externalRevision + uploadRevision);
  const reviews = useResource<{ items: Review[]; total: number }>("/api/team/workflows/runs?view=inbox", false, revision + externalRevision);
  const data = state.data;
  const percent = data && data.quota_bytes > 0 ? Math.min(100, data.used_bytes / data.quota_bytes * 100) : 0;
  function fileRows(items: TeamFile[]) {
    return items.length ? <ul className={styles.rows}>{items.map(file => <li key={file.id}>
      <span className={styles.fileIcon}><Files size={18} /></span>
      <div><strong>{file.name}</strong><span>{file.owner_name} / {storageSize(file.size_bytes)}</span><time dateTime={file.created_at}>{dateTime(file.created_at)}</time></div>
      <Link href={`/team/drives?${new URLSearchParams({ owner: String(file.owner_id), folder: file.folder, view: "list" })}`} aria-label={`Open folder for ${file.name}`} title={`Open folder for ${file.name}`}><FolderOpen size={18} /></Link>
    </li>)}</ul> : <p className={styles.empty}>No available files</p>;
  }
  return <div className={styles.dashboard}>
    <div className={styles.heading}><h2>Team overview</h2><button type="button" title="Refresh team overview" aria-label="Refresh team overview" disabled={state.loading || reviews.loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={17} /></button></div>
    <LoadState {...state} retry={() => setRevision(value => value + 1)} />
    {data && <>
      <div className={styles.stats}>
        <article className={`${styles.card} ${styles.storage}`}><h3><HardDrive size={18} />Team storage</h3><strong className={styles.value}>{storageSize(data.used_bytes)}</strong><p>of {storageSize(data.quota_bytes)} allocated</p><div className={styles.track} role="progressbar" aria-label="Team storage usage" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-valuetext={`${storageSize(data.used_bytes)} used of ${storageSize(data.quota_bytes)} allocated`}><span style={{ width: `${percent}%` }} /></div><p>{data.used_bytes > data.quota_bytes ? "Allocation exceeded" : data.quota_bytes ? `${storageSize(data.remaining_bytes)} remaining` : "No team allocation"}</p></article>
        <article className={styles.card}><h3><Users size={18} />Active members</h3><strong className={styles.value}>{data.members}</strong><Link className={styles.cardLink} href="#team-members">View members <ArrowUpRight size={16} /></Link></article>
        <article className={styles.card}><h3><Files size={18} />Available files</h3><strong className={styles.value}>{data.files}</strong><p>Ready files in this team</p><Link className={styles.cardLink} href="#team-members">Team drives <ArrowUpRight size={16} /></Link></article>
        <article className={styles.card}><h3><ClipboardCheck size={18} />Awaiting your review</h3><strong className={styles.value}>{data.pending_reviews}</strong><Link className={styles.cardLink} href="/team/workflows?view=inbox">Open approvals <ArrowUpRight size={16} /></Link></article>
      </div>
      <div className={styles.widgets}>
        <article className={styles.card}><div className={styles.widgetHeading}><h3>Recent uploads</h3><Link href="#team-members" title="Browse team drives" aria-label="Browse team drives"><ArrowUpRight size={18} /></Link></div>{data.manager_can_view_drives ? fileRows(data.recent_files) : <p className={styles.empty}><LockKeyhole size={20} />Team drive access is disabled by your company admin.</p>}</article>
        <article className={styles.card}><div className={styles.widgetHeading}><h3>Largest files</h3><Link href="#team-members" title="Browse member drives" aria-label="Browse member drives"><ArrowUpRight size={18} /></Link></div>{data.manager_can_view_drives ? fileRows(data.largest_files) : <p className={styles.empty}><LockKeyhole size={20} />Team drive access is disabled by your company admin.</p>}</article>
        <article className={styles.card}><div className={styles.widgetHeading}><h3>Pending approvals</h3><Link href="/team/workflows?view=inbox">View all <ArrowUpRight size={16} /></Link></div><LoadState {...reviews} retry={() => setRevision(value => value + 1)} />{reviews.data && (reviews.data.items.length ? <ul className={styles.rows}>{reviews.data.items.slice(0, 5).map(review => <li key={review.id}><span className={styles.fileIcon}><ClipboardCheck size={18} /></span><div><strong>{review.file_name}</strong><span>{review.workflow_name}</span><time dateTime={review.created_at}>{dateTime(review.created_at)}</time></div><Link href={`/team/workflows/runs/${review.id}`} aria-label={`Review ${review.file_name}`} title={`Review ${review.file_name}`}><ArrowUpRight size={18} /></Link></li>)}</ul> : <p className={styles.empty}>No pending approvals</p>)}</article>
      </div>
    </>}
  </div>;
}