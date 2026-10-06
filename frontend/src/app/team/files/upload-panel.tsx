"use client";

import { useEffect, useId, useState, useSyncExternalStore, type CSSProperties } from "react";
import { CheckCircle2, ChevronDown, ChevronUp, CircleAlert, Clock3, File, RotateCcw, X } from "lucide-react";
import { getUploadQueue, type UploadItem } from "@/lib/upload-queue";
import drive from "./files.module.css";

const empty: UploadItem[] = [];

export default function UploadPanel({ owner }: { owner: string }) {
  const queue = getUploadQueue(owner);
  const items = useSyncExternalStore(queue.subscribe, queue.snapshot, () => empty);
  const [collapsed, setCollapsed] = useState(false);
  const listId = useId();
  const active = items.filter(item => item.status === "queued" || item.status === "uploading").length;
  const complete = items.filter(item => item.status === "complete").length;
  const failed = items.filter(item => item.status === "failed").length;
  const totalBytes = items.reduce((total, item) => total + item.size, 0);
  const percent = totalBytes ? Math.floor(items.reduce((total, item) => total + item.size * item.progress, 0) / totalBytes) : complete === items.length ? 100 : 0;

  useEffect(() => {
    if (!active) return;
    function warn(event: BeforeUnloadEvent) { event.preventDefault(); event.returnValue = ""; }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [active]);

  if (!items.length) return null;
  return <aside className={drive.uploadPanel} aria-label="Upload progress">
    <header className={drive.panelHeader}>
      <h2>{active ? `Uploading ${items.length} ${items.length === 1 ? "file" : "files"}` : failed ? "Uploads need attention" : `${complete} ${complete === 1 ? "upload" : "uploads"} complete`}</h2>
      <button type="button" className={drive.panelButton} aria-expanded={!collapsed} aria-controls={listId} title={collapsed ? "Expand uploads" : "Minimize uploads"} aria-label={collapsed ? "Expand uploads" : "Minimize uploads"} onClick={() => setCollapsed(value => !value)}>{collapsed ? <ChevronUp size={18} /> : <ChevronDown size={18} />}</button>
      {!active && <button type="button" className={drive.panelButton} title="Dismiss upload panel" aria-label="Dismiss upload panel" onClick={() => queue.dismiss()}><X size={18} /></button>}
    </header>
    {active > 0 && <progress className={drive.overallProgress} max={100} value={percent} aria-label="Overall upload progress" />}
    <div id={listId} hidden={collapsed} className={drive.panelBody}>
      <div className={drive.queueSummary}><span role="status">{complete} of {items.length} complete{failed ? ` / ${failed} failed` : ""}</span>{active > 0 && <span>{percent}%</span>}</div>
      <ul className={drive.queueList}>
      {items.map(item => <li className={drive.queueItem} key={item.key} data-status={item.status}>
        <span className={drive.queueFileIcon}><File size={23} strokeWidth={1.5} aria-hidden="true" /></span>
        <div className={drive.queueInfo}><strong title={`${item.name} / ${item.folder || "My drive"}`}>{item.name}</strong>
          {item.status === "uploading" && <span className={drive.activeUpload}>{item.progress >= 99 ? "Finalizing..." : `${item.progress}% uploaded`}</span>}
          {item.status === "queued" && <span>Queued</span>}
          {item.status === "complete" && <span>Upload complete</span>}
          {item.status === "failed" && <p className={drive.queueError}><CircleAlert size={13} aria-hidden="true" />{item.error}</p>}
        </div>
        {item.status === "uploading" && <span className={drive.progressRing} role="progressbar" aria-label={`Uploading ${item.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={item.progress} aria-valuetext={item.progress >= 99 ? "Finalizing" : `${item.progress}%`} style={{ "--progress": `${item.progress}%` } as CSSProperties} />}
        {item.status === "queued" && <Clock3 size={20} className={drive.uploadWaiting} aria-label="Queued" />}
        {item.status === "complete" && <CheckCircle2 size={22} className={drive.uploadSuccess} aria-label="Complete" />}
        {item.status === "failed" && <button type="button" className={drive.panelButton} title={`Retry ${item.name}`} aria-label={`Retry ${item.name}`} onClick={() => queue.retry(item.key)}><RotateCcw size={17} /></button>}
      </li>)}
      </ul>
    </div>
  </aside>;
}