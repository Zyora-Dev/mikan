"use client";

import { useState, type FormEvent } from "react";
import { Save } from "lucide-react";
import { MAX_FILE_BYTES, teamRequest } from "@/lib/team-client";
import { LoadState, useResource } from "./workflow-ui";
import shared from "@/app/admin/companies/companies.module.css";
import styles from "./workflows.module.css";

type Policy = { enabled: boolean; max_file_bytes: number };
type Settings = { policy: Policy | null };
const api = "/api/company/teams/workflows/uploads";
const units = [{ label: "TiB", bytes: 1024 ** 4 }, { label: "GiB", bytes: 1024 ** 3 }, { label: "MiB", bytes: 1024 ** 2 }, { label: "Bytes", bytes: 1 }];

export default function UploadSettings() {
  const [revision, setRevision] = useState(0);
  const state = useResource<Settings>(api, true, revision);
  return state.data ? <SettingsForm key={revision} initial={state.data} /> : <LoadState {...state} retry={() => setRevision(value => value + 1)} />;
}

function SettingsForm({ initial }: { initial: Settings }) {
  const [policy, setPolicy] = useState<Policy>(initial.policy || { enabled: false, max_file_bytes: MAX_FILE_BYTES });
  const [unit, setUnit] = useState(() => units.find(option => (initial.policy?.max_file_bytes || MAX_FILE_BYTES) % option.bytes === 0)!.bytes);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { const result = await teamRequest<{ detail: string }>(api, policy); setMessage(result.detail); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to save settings."); }
    finally { setBusy(false); }
  }
  return <form className={styles.form} onSubmit={event => void save(event)}>
    <h2>Private uploads</h2>
    {error && <p role="alert" className={shared.formError}>{error}</p>}{message && <p role="status" className={shared.notice}>{message}</p>}
    <fieldset disabled={busy} className={styles.fieldset}><div className={styles.fields}>
      <label className={styles.check}><input type="checkbox" checked={policy.enabled} onChange={event => setPolicy({ ...policy, enabled: event.target.checked })} />Uploads enabled</label>
      <label className={styles.field}>Maximum file size<input className={styles.sizeInput} required type="number" min={1 / unit} max={MAX_FILE_BYTES / unit} step="any" value={policy.max_file_bytes / unit || ""} onChange={event => setPolicy({ ...policy, max_file_bytes: Math.round(Number(event.target.value) * unit) })} /></label>
      <label className={styles.field}>Size unit<select className={styles.sizeInput} value={unit} onChange={event => setUnit(Number(event.target.value))}>{units.map(option => <option key={option.label} value={option.bytes}>{option.label}</option>)}</select></label>
    </div></fieldset>
    <div className={styles.actions}><button className={shared.primary} disabled={busy}><Save size={16} />{busy ? "Saving..." : "Save settings"}</button></div>
  </form>;
}