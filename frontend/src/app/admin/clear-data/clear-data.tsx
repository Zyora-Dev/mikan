"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Eye, LoaderCircle, ShieldCheck, Trash2, X } from "lucide-react";
import { teamRequest, TeamRequestError } from "@/lib/team-client";
import CompanyPicker from "../company-picker";
import styles from "../companies/companies.module.css";
import local from "../management.module.css";

const categories = ["companies", "admins", "members", "files", "folders"] as const;
type Category = typeof categories[number];
type Selection = Record<Category, boolean>;
type Preview = { counts: Record<Category | "versions" | "teams" | "workflows" | "workflow_runs", number>; blockers: string[]; fingerprint: string; remaining_files: number };
const empty: Selection = { companies: false, admins: false, members: false, files: false, folders: false };
const labels: Record<Category, string> = { companies: "Companies", admins: "Company admins", members: "Members and managers", files: "Files", folders: "Folders" };
const endpoint = "/api/admin/management/clear";

export default function ClearData() {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const running = useRef(false);
  const mounted = useRef(true);
  const [company, setCompany] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [companyRevision, setCompanyRevision] = useState(0);
  const [selection, setSelection] = useState<Selection>(empty);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [removed, setRemoved] = useState(0);
  useEffect(() => {
    mounted.current = true;
    function guard(event: BeforeUnloadEvent) { if (running.current) event.preventDefault(); }
    window.addEventListener("beforeunload", guard);
    return () => { mounted.current = false; window.removeEventListener("beforeunload", guard); };
  }, []);
  const selectedCount = categories.reduce((total, category) => total + Number(selection[category]), 0);
  const recordCount = preview ? categories.reduce((total, category) => total + (selection[category] ? preview.counts[category] : 0), 0) : 0;
  const scope = { ...selection, company_id: company === "all" ? null : Number(company) };
  function invalidate() { setPreview(null); setConfirmation(""); setError(""); setNotice(""); setRemoved(0); }
  function report(failure: unknown) {
    if (failure instanceof TeamRequestError && failure.status === 401) router.replace("/admin/login");
    setError(failure instanceof Error ? failure.message : "Unable to complete the request.");
  }
  async function review() {
    if (loading || busy || !company || !selectedCount) return;
    setLoading(true); invalidate();
    try { setPreview(await teamRequest<Preview>(`${endpoint}/preview`, scope)); }
    catch (failure) { report(failure); }
    finally { setLoading(false); }
  }
  async function clear() {
    if (running.current || !preview || preview.blockers.length || confirmation !== "CLEAR DATA" || !recordCount) return;
    running.current = true; setBusy(true); setError(""); setRemoved(0);
    const payload = { ...scope, confirmation, fingerprint: preview.fingerprint };
    const startingFiles = selection.files ? preview.remaining_files : 0;
    let completed = 0;
    try {
      for (let batch = 0; batch <= startingFiles; batch++) {
        const result = await teamRequest<{ done: boolean; detail?: string; remaining_files?: number }>(`${endpoint}/execute`, payload, undefined, 130000);
        if (!mounted.current) return;
        if (result.done) {
          setNotice(result.detail || "Selected data cleared."); setPreview(null); setSelection(empty); setConfirmation("");
          setCompany(""); setCompanyName(""); setCompanyRevision(value => value + 1);
          dialog.current?.close(); router.refresh(); return;
        }
        completed += 1; setRemoved(completed);
      }
      throw new Error("Cleanup did not finish. Review a new preview before continuing.");
    } catch (failure) {
      if (mounted.current) {
        report(failure);
        setNotice(`${completed} file cleanups confirmed in this attempt. A timed-out request may also have completed. Removed files cannot be recovered; failed storage cleanup may continue through background retries. Review a fresh preview before continuing.`);
        setPreview(null); setConfirmation(""); dialog.current?.close();
      }
    } finally { running.current = false; if (mounted.current) setBusy(false); }
  }
  return <section className={styles.section} aria-labelledby="clear-title">
    <div className={styles.heading}><div><p className={styles.eyebrow}>SUPER ADMIN</p><h1 id="clear-title">Clear Data</h1></div></div>
    <div className={local.content}>
      <CompanyPicker key={companyRevision} value={company} all disabled={busy || loading} onChange={(value, name) => { setCompany(value); setCompanyName(name); invalidate(); }} />
      <fieldset className={local.selection} disabled={busy || loading}><legend>Data to permanently remove</legend>{categories.map(category => <label key={category} className={local.category}><input type="checkbox" checked={selection[category]} onChange={event => { setSelection({ ...selection, [category]: event.target.checked }); invalidate(); }} /><span>{labels[category]}</span>{preview && <strong>{preview.counts[category].toLocaleString()}</strong>}</label>)}</fieldset>
      <div className={local.warning}><AlertTriangle size={20} aria-hidden="true" /><p>Deletion is permanent, including stored file contents and all revisions. Back up any data you need before confirming.</p>
        {(selection.files || selection.members || selection.companies) && <p>Related workflow runs, tasks, notifications and automation jobs will also be removed.</p>}
        {selection.files && <p>File shares, revisions and file activity will be removed.</p>}
        {selection.members && <p>Member sessions, invitations and workflow definitions will be removed.</p>}
        {selection.admins && <p>Company-admin sessions and account notifications will be removed.</p>}
        {selection.companies && <p>Company teams, policies, workflow definitions, notifications and history will be removed.</p>}
      </div>
      <p className={local.preserved}><ShieldCheck size={17} aria-hidden="true" /> Super-admin accounts and platform integrations are preserved.</p>
      {preview && <div aria-live="polite"><p>{recordCount.toLocaleString()} selected records{selection.files ? `, including ${preview.counts.versions.toLocaleString()} file revisions` : ""}.</p>{preview.blockers.length > 0 && <ul className={local.warning}>{preview.blockers.map(message => <li key={message}>{message}</li>)}</ul>}</div>}
      {error && <p className={styles.formError} role="alert">{error}</p>}
      {notice && <p className={styles.notice} role="status">{notice}</p>}
      <div className={local.controls}><button className={styles.secondary} disabled={loading || busy || !company || !selectedCount} onClick={() => void review()}>{loading ? <LoaderCircle size={17} className={styles.spin} /> : <Eye size={17} />}Preview selected data</button><button className={`${styles.primary} ${local.danger}`} disabled={busy || loading || !preview || !!preview.blockers.length || !recordCount} onClick={() => { setConfirmation(""); dialog.current?.showModal(); }}><Trash2 size={17} />Clear selected data</button></div>
    </div>
    <dialog ref={dialog} className={styles.dialog} aria-labelledby="clear-confirm-title" onCancel={event => { if (busy) event.preventDefault(); }}>
      <form onSubmit={event => { event.preventDefault(); void clear(); }}>
        <header className={styles.dialogHeader}><h2 id="clear-confirm-title">Permanently clear selected data?</h2><button className={styles.iconButton} type="button" disabled={busy} title="Close" aria-label="Close" onClick={() => dialog.current?.close()}><X size={20} /></button></header>
        <div className={styles.formBody}>
          <p className={local.scopeName}>{companyName}: {recordCount.toLocaleString()} records.</p>
          <ul className={local.summary}>{categories.filter(category => selection[category]).map(category => <li key={category}>{labels[category]}: {preview?.counts[category].toLocaleString()}</li>)}</ul>
          <p className={local.warning}>Files are removed in batches before database records. A partial failure does not restore already removed files. Keep this page open until cleanup finishes.</p>
          <label className={styles.field}>Type CLEAR DATA to confirm<input autoFocus autoComplete="off" value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)} /></label>
          {busy && <div role="status"><p>{selection.files ? `${removed} of ${preview?.remaining_files || 0} file cleanups confirmed` : "Removing selected records..."}</p><progress className={local.progress} max={Math.max(1, preview?.remaining_files || 0)} value={removed} /></div>}
        </div>
        <footer className={styles.dialogFooter}><button type="button" className={styles.secondary} disabled={busy} onClick={() => dialog.current?.close()}>Cancel</button><button className={`${styles.primary} ${local.danger}`} disabled={busy || confirmation !== "CLEAR DATA"}>{busy ? <LoaderCircle size={17} className={styles.spin} /> : <Trash2 size={17} />}{busy ? "Clearing..." : "Permanently clear"}</button></footer>
      </form>
    </dialog>
  </section>;
}