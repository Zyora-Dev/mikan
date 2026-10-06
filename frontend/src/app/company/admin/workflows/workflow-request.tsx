"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { ArrowLeft, Check, Download, RefreshCw, X } from "lucide-react";
import { teamRequest } from "@/lib/team-client";
import { outcomeLabel, type Outcome } from "@/lib/workflow";
import { dateTime, LoadState, useResource } from "./workflow-ui";
import shared from "@/app/admin/companies/companies.module.css";
import styles from "./workflows.module.css";

type RequestDetail = {
  id: string; workflow_name: string; workflow_version: number; file_id: string; file_name: string; status: string;
  current_node: string; created_at: string; submitter_id: number; enabled?: boolean;
  tasks: { id: number; node_id: string; reviewer_id: number; reviewer_name: string; status: string; comment: string; decided_at: string | null }[];
  events: { id: number; kind: string; actor_name: string; detail: string; created_at: string }[];
};

export default function WorkflowRequest({ identifier, company, accountId }: { identifier: string; company: boolean; accountId?: number }) {
  const [revision, setRevision] = useState(0);
  const [comment, setComment] = useState("");
  const [cancelComment, setCancelComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const base = company ? "/company/admin/workflows" : "/team/workflows";
  const api = `${company ? "/api/company/teams" : "/api/team"}/workflows/runs/${identifier}`;
  const state = useResource<RequestDetail>(api, company, revision);
  const run = state.data;
  const pending = run?.status === "pending";
  const assigned = run?.tasks.some(task => task.reviewer_id === accountId && task.node_id === run.current_node && task.status === "pending");
  const canDecide = !company && pending && assigned && run?.enabled;
  const canCancel = pending && (company || run?.submitter_id === accountId);
  const canDownload = !company && (run?.submitter_id === accountId || (pending && assigned && run?.enabled));

  async function act(outcome: Outcome | "cancel") {
    if (busy) return;
    if (outcome !== "approved" && outcome !== "cancel" && !comment.trim()) { setError("Add a reason for rejection or requested changes."); return; }
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await teamRequest<{ detail: string }>(`${api}/${outcome === "cancel" ? "cancel" : "decide"}`, outcome === "cancel" ? { comment: cancelComment } : { outcome, comment });
      dialog.current?.close(); setComment(""); setCancelComment(""); setMessage(response.detail); setRevision(value => value + 1);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to update request."); }
    finally { setBusy(false); }
  }

  return <section className={styles.section}>
    <Link className={styles.back} href={`${base}?view=${company ? "runs" : "mine"}`}><ArrowLeft size={16} />Requests</Link>
    <div className={styles.heading}><h1>{run?.file_name || "Workflow request"}</h1><div className={styles.actions}>
      <button className={shared.iconButton} title="Refresh request" aria-label="Refresh request" disabled={busy || state.loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={17} /></button>
      {canDownload && run && <a className={shared.secondary} href={`/api/team/files/${run.file_id}/content`} download={run.file_name} target="_blank" rel="noopener noreferrer"><Download size={16} />Download file</a>}
      {canCancel && <button className={shared.secondary} disabled={busy} onClick={() => { setError(""); dialog.current?.showModal(); }}><X size={16} />Cancel request</button>}
    </div></div>
    {error && <p className={shared.formError} role="alert">{error}</p>}{message && <p className={shared.notice} role="status">{message}</p>}
    <LoadState {...state} retry={() => setRevision(value => value + 1)} />
    {run && <div className={styles.detailGrid}><div>
      <dl className={styles.details}><div><dt>Workflow</dt><dd>{run.workflow_name} / Version {run.workflow_version}</dd></div><div><dt>Status</dt><dd><span className={styles.badge} data-status={run.status}>{outcomeLabel(run.status)}</span>{pending && run.enabled === false && " / Paused by admin"}</dd></div><div><dt>Submitted</dt><dd>{dateTime(run.created_at)}</dd></div></dl>
      <h2>Activity</h2><ol className={styles.timeline}>{run.events.map(event => <li key={event.id}><strong>{outcomeLabel(event.kind)} / {event.actor_name}</strong><p>{event.detail}</p><small>{dateTime(event.created_at)}</small></li>)}</ol>
    </div><div>
      {canDecide && <form className={styles.form} onSubmit={event => { event.preventDefault(); void act("approved"); }}><h2>Your decision</h2><label className={styles.field}>Comment<textarea maxLength={2000} value={comment} disabled={busy} onChange={event => setComment(event.target.value)} /></label><div className={styles.actions}><button className={shared.primary} disabled={busy} type="submit"><Check size={16} />Approve</button><button className={shared.secondary} disabled={busy} type="button" onClick={() => void act("changes_requested")}><RefreshCw size={16} />Request changes</button><button className={shared.secondary} disabled={busy} type="button" onClick={() => void act("rejected")}><X size={16} />Reject</button></div></form>}
      <h2>Reviewers</h2><ol className={styles.timeline}>{run.tasks.map(task => <li key={task.id}><strong>{task.reviewer_name}{task.reviewer_id === accountId ? " (You)" : ""}</strong><p><span className={styles.badge} data-status={task.status}>{outcomeLabel(task.status)}</span></p>{task.comment && <p>{task.comment}</p>}{task.decided_at && <small>{dateTime(task.decided_at)}</small>}</li>)}</ol>
    </div></div>}
    <dialog ref={dialog} className={shared.dialog} onCancel={event => { if (busy) event.preventDefault(); }}><form className={styles.section} onSubmit={event => { event.preventDefault(); void act("cancel"); }}><div className={styles.heading}><h2>Cancel request?</h2><button className={shared.iconButton} type="button" aria-label="Close" disabled={busy} onClick={() => dialog.current?.close()}><X size={18} /></button></div><p>Pending reviews will close and reviewer file access will end.</p><label className={styles.field}>Reason (optional)<textarea value={cancelComment} maxLength={2000} disabled={busy} onChange={event => setCancelComment(event.target.value)} /></label>{error && <p className={shared.formError} role="alert">{error}</p>}<div className={styles.toolbar}><button type="button" className={shared.secondary} disabled={busy} onClick={() => dialog.current?.close()}>Keep request</button><button className={shared.primary} disabled={busy}>{busy ? "Cancelling..." : "Cancel request"}</button></div></form></dialog>
  </section>;
}