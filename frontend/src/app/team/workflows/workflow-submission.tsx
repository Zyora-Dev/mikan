"use client";

import Link from "next/link";
import { useDeferredValue, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, File, Send } from "lucide-react";
import { teamRequest } from "@/lib/team-client";
import type { Person, Step } from "@/lib/workflow";
import { LoadState, Pagination, PeoplePicker, useResource } from "../../company/admin/workflows/workflow-ui";
import shared from "@/app/admin/companies/companies.module.css";
import styles from "../../company/admin/workflows/workflows.module.css";

type OwnFile = { id: string; name: string; size_bytes: number };
const api = "/api/team/workflows";

function FixedReviewers({ step, identifier }: { step: Step; identifier: string }) {
  const [revision, setRevision] = useState(0);
  const state = useResource<{ items: Person[] }>(`${api}/people?workflow_id=${identifier}&node_id=${encodeURIComponent(step.id)}`, false, revision);
  return <><LoadState {...state} retry={() => setRevision(value => value + 1)} />{state.data && <div className={styles.chips}>{state.data.items.map(person => <span key={person.id} className={styles.chip}>{person.name}</span>)}{state.data.items.length !== new Set(step.data.reviewer_ids).size && <p className={shared.formError}>A configured reviewer is unavailable. Contact your company admin.</p>}</div>}</>;
}

export default function WorkflowSubmission({ identifier }: { identifier: string }) {
  const router = useRouter();
  const [revision, setRevision] = useState(0);
  const [search, setSearch] = useState("");
  const query = useDeferredValue(search);
  const [page, setPage] = useState(1);
  const [file, setFile] = useState<OwnFile | null>(null);
  const [reviewers, setReviewers] = useState<Record<string, number[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestKey = useRef("");
  const submittedPayload = useRef("");
  const flow = useResource<{ id: number; name: string; version: number; steps: Step[] }>(`${api}/${identifier}`, false, revision);
  const files = useResource<{ items: OwnFile[]; total: number }>(`${api}/files?search=${encodeURIComponent(query)}&page=${page}`, false, revision);

  async function submit() {
    if (!file || !flow.data || busy) return;
    const missing = flow.data.steps.find(step => step.data.reviewer_mode === "tagged" && !reviewers[step.id]?.length);
    if (missing) { setError(`Tag at least one reviewer for ${missing.data.label}.`); return; }
    const payload = JSON.stringify({ file_id: file.id, reviewers, version: flow.data.version });
    if (payload !== submittedPayload.current) { requestKey.current = crypto.randomUUID(); submittedPayload.current = payload; }
    setBusy(true); setError("");
    try {
      const result = await teamRequest<{ id: string }>(`${api}/${identifier}/submit`, { file_id: file.id, reviewers, version: flow.data.version, request_key: requestKey.current });
      router.push(`/team/workflows/runs/${result.id}`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to submit file."); setBusy(false); }
  }

  return <section className={styles.section}><Link className={styles.back} href="/team/workflows"><ArrowLeft size={16} />Workflows</Link><div className={styles.heading}><h1>{flow.data?.name || "Submit file"}</h1></div>
    <LoadState {...flow} retry={() => setRevision(value => value + 1)} />
    {flow.data && <form className={styles.form} onSubmit={event => { event.preventDefault(); void submit(); }}><fieldset className={styles.fieldset} disabled={busy}><div className={styles.fields}>
      <label className={styles.field}>File from your drive<input type="search" placeholder="Search files" maxLength={100} value={search} onChange={event => { setSearch(event.target.value); setPage(1); }} /></label>
      {file && <p className={styles.badge}><File size={16} />{file.name}</p>}
      <LoadState {...files} retry={() => setRevision(value => value + 1)} />
      {files.data && <>{files.data.items.length ? <div className={styles.choices}>{files.data.items.map(item => <label key={item.id} className={styles.check}><input type="radio" name="file" checked={file?.id === item.id} onChange={() => setFile(item)} /><span>{item.name} <small>({new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 }).format(item.size_bytes / 1024)} KB)</small></span></label>)}</div> : <div className={shared.empty}>No ready files found in your drive.</div>}<Pagination total={files.data.total} page={page} setPage={setPage} /></>}
      {flow.data.steps.map(step => <fieldset className={styles.group} key={step.id}><legend>{step.data.label}</legend><div className={styles.fields}><p className={styles.sub}>{step.data.rule === "all" ? "All reviewers must approve" : "Any one reviewer can approve"}</p>{step.data.reviewer_mode === "fixed" ? <FixedReviewers step={step} identifier={identifier} /> : <PeoplePicker url={`${api}/people?workflow_id=${identifier}&node_id=${encodeURIComponent(step.id)}`} company={false} selected={reviewers[step.id] || []} onChange={people => setReviewers(previous => ({ ...previous, [step.id]: people }))} label="Tag reviewers" disabled={busy} />}</div></fieldset>)}
    </div></fieldset>{error && <p className={shared.formError} role="alert">{error}</p>}<div className={styles.actions}><button className={shared.primary} type="submit" disabled={busy || !file}><Send size={16} />{busy ? "Submitting..." : "Submit for approval"}</button></div></form>}
  </section>;
}