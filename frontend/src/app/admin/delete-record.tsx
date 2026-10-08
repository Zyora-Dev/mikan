"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, Trash2, X } from "lucide-react";
import styles from "./companies/companies.module.css";

export default function DeleteRecord({ name, endpoint, onDeleted, method = "DELETE", loginPath = "/admin/login" }: { name: string; endpoint: string; onDeleted: () => void; method?: "DELETE" | "POST"; loginPath?: string }) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function remove() {
    if (busy || confirmation !== "DELETE") return;
    setBusy(true); setError("");
    try {
      const response = await fetch(endpoint, { method, headers: { "Content-Type": "application/json" }, body: "{}", signal: AbortSignal.timeout(25000) });
      if (response.status === 401) { router.replace(loginPath); return; }
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Unable to delete this record.");
      dialog.current?.close(); onDeleted();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to delete this record."); }
    finally { setBusy(false); }
  }
  return <>
    <button type="button" className={styles.iconButton} aria-label={`Delete ${name}`} title={`Delete ${name}`} onClick={() => { setConfirmation(""); setError(""); dialog.current?.showModal(); }}><Trash2 size={16} /></button>
    <dialog ref={dialog} className={styles.dialog} aria-label={`Delete ${name}`} onCancel={event => { if (busy) event.preventDefault(); }}>
      <form onSubmit={event => { event.preventDefault(); void remove(); }}>
        <header className={styles.dialogHeader}><h2>Delete {name}?</h2><button type="button" className={styles.iconButton} disabled={busy} title="Close" aria-label="Close" onClick={() => dialog.current?.close()}><X size={20} /></button></header>
        <div className={styles.formBody}><p>This permanently removes the record. Records with dependent data cannot be deleted here.</p><label className={styles.field}>Type DELETE to confirm<input autoFocus autoComplete="off" value={confirmation} onChange={event => setConfirmation(event.target.value)} disabled={busy} /></label>{error && <p className={styles.formError} role="alert">{error}</p>}</div>
        <footer className={styles.dialogFooter}><button type="button" className={styles.secondary} disabled={busy} onClick={() => dialog.current?.close()}>Cancel</button><button className={styles.primary} disabled={busy || confirmation !== "DELETE"}>{busy ? <LoaderCircle size={16} className={styles.spin} /> : <Trash2 size={16} />}Delete</button></footer>
      </form>
    </dialog>
  </>;
}