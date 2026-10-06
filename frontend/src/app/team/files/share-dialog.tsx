"use client";

import { useDeferredValue, useEffect, useRef, useState, type FormEvent } from "react";
import { Copy, Mail, Share2, UserMinus, X } from "lucide-react";
import { teamRequest } from "@/lib/team-client";
import { LoadState, Pagination, useResource } from "../../company/admin/workflows/workflow-ui";
import shared from "../../admin/companies/companies.module.css";
import drive from "./files.module.css";

type Member = { id: number; name: string; email: string; team_name: string; status?: string; email_status?: string; permission?: "view" | "edit" };

const emailLabels: Record<string, string> = { queued: "Email queued", retry: "Email retry pending", accepted: "Email sent", failed: "Email failed", cancelled: "Email cancelled" };

function MemberSuggestions({ base, query, selected, busy, revision, retry, onSelect }: {
  base: string; query: string; selected: Member[]; busy: boolean; revision: number;
  retry: () => void; onSelect: (person: Member, checked: boolean) => void;
}) {
  const members = useResource<{ items: Member[] }>(`${base}/share-members?search=${encodeURIComponent(query)}`, false, revision);
  return <>
    <LoadState {...members} retry={retry} />
    {members.data && <ul className={drive.sharePeople} aria-label="Company member suggestions">{members.data.items.length ? members.data.items.map(person => <li key={person.id}><label><input type="checkbox" checked={selected.some(item => item.id === person.id)} disabled={busy || (selected.length >= 50 && !selected.some(item => item.id === person.id))} onChange={event => onSelect(person, event.target.checked)} /><span><strong>{person.name}</strong><span>{person.email}</span><small>{person.team_name}</small></span></label></li>) : <li className={drive.shareEmpty}>No matching members available.</li>}</ul>}
  </>;
}

export default function ShareDialog({ file, onClose }: { file: { id: string; name: string }; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [search, setSearch] = useState("");
  const query = useDeferredValue(search.trim());
  const [selected, setSelected] = useState<Member[]>([]);
  const [permission, setPermission] = useState<"view" | "edit">("view");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const base = `/api/team/files/${file.id}`;
  const access = useResource<{ items: Member[]; total: number }>(`${base}/shares?page=${page}`, false, revision);
  const link = useResource<{ url: string; public_access: boolean }>(`${base}/link`, false, revision);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);

  async function changeAccess(publicAccess: boolean) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await teamRequest(`${base}/link`, { public_access: publicAccess });
      if (publicAccess) setPermission("view");
      setRevision(value => value + 1);
      setMessage(publicAccess ? "Anyone with the link can view and download. Edit permissions changed to View." : "Link access restricted to you and members with access.");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to update link access."); }
    finally { setBusy(false); }
  }

  async function copyLink() {
    if (!link.data) return;
    setError(""); setMessage("");
    try { await navigator.clipboard.writeText(link.data.url); setMessage("Link copied."); }
    catch { setError("Clipboard unavailable. Select and copy the file URL."); }
  }

  async function retryEmail(person: Member) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await teamRequest(`${base}/shares/${person.id}/retry-email`, {});
      setRevision(value => value + 1); setMessage("Email notification queued.");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to retry email."); }
    finally { setBusy(false); }
  }

  async function share(event: FormEvent) {
    event.preventDefault();
    if (busy || !selected.length) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await teamRequest(`${base}/shares`, { recipient_ids: selected.map(person => person.id), permission: link.data?.public_access ? "view" : permission });
      setSelected([]); setSearch(""); setPage(1); setRevision(value => value + 1); setMessage("File shared. Email notifications queued.");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to share file."); }
    finally { setBusy(false); }
  }

  async function changePermission(person: Member, value: string) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await teamRequest(`${base}/shares/${person.id}/permission`, { permission: value });
      setRevision(current => current + 1); setMessage(`Access updated for ${person.name}.`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to change permission."); }
    finally { setBusy(false); }
  }

  async function remove(person: Member) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await teamRequest(`${base}/shares/${person.id}/remove`, {});
      setPage(1); setRevision(value => value + 1); setMessage(`Access removed for ${person.name}.`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to remove access."); }
    finally { setBusy(false); }
  }

  return <dialog ref={dialog} className={`${shared.dialog} ${drive.shareDialog}`} aria-labelledby="share-file-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <form onSubmit={event => void share(event)}>
      <header className={shared.dialogHeader}><h2 id="share-file-title">Share file</h2><button type="button" className={shared.iconButton} disabled={busy} title="Close" aria-label="Close sharing" onClick={onClose}><X size={18} /></button></header>
      <div className={drive.shareBody}>
        <p className={drive.shareFilename}>{file.name}</p>
        {error && <p role="alert" className={shared.formError}>{error}</p>}
        {message && <p role="status" className={shared.notice}>{message}</p>}
        <section className={drive.shareLink} aria-label="File link and access">
          <fieldset className={drive.shareMode} disabled={busy || !link.data}><legend>General access</legend><div><label><input type="radio" name="file-access" value="restricted" checked={link.data?.public_access === false} onChange={() => void changeAccess(false)} /><span>Restricted</span></label><label><input type="radio" name="file-access" value="public" checked={link.data?.public_access === true} onChange={() => void changeAccess(true)} /><span>Anyone with the link</span></label></div></fieldset>
          <label className={drive.filterField}>File URL<div className={drive.shareCopy}><input aria-label="File URL" readOnly value={link.data?.url || ""} placeholder="Loading link..." onFocus={event => event.currentTarget.select()} /><button type="button" className={shared.secondary} disabled={busy || !link.data} onClick={() => void copyLink()}><Copy size={16} />Copy link</button></div></label>
          <LoadState {...link} retry={() => setRevision(value => value + 1)} />
        </section>
        <label className={drive.filterField}>Company members<input type="search" placeholder="Search by name or email" maxLength={100} value={search} disabled={busy} onChange={event => setSearch(event.target.value)} /></label>
        {!!selected.length && <ul className={drive.shareSelected} aria-label="Selected recipients">{selected.map(person => <li key={person.id}><span>{person.name} <small>{person.email}</small></span><button type="button" className={shared.iconButton} disabled={busy} title={`Remove ${person.name}`} aria-label={`Remove ${person.name} from selection`} onClick={() => setSelected(current => current.filter(item => item.id !== person.id))}><X size={16} /></button></li>)}</ul>}
        {!!query && query === search.trim() && <MemberSuggestions base={base} query={query} selected={selected} busy={busy} revision={revision} retry={() => setRevision(value => value + 1)} onSelect={(person, checked) => setSelected(current => checked ? [...current.filter(item => item.id !== person.id), person] : current.filter(item => item.id !== person.id))} />}
        {!!selected.length && <label className={drive.permissionField}>Recipient access<select value={link.data?.public_access ? "view" : permission} disabled={busy || !link.data} onChange={event => setPermission(event.target.value as "view" | "edit")}><option value="view">View</option><option value="edit" disabled={link.data?.public_access !== false}>Edit</option></select></label>}
        <section className={drive.shareAccess} aria-labelledby="share-access-title"><header><h3 id="share-access-title">People with access</h3></header>
          <LoadState {...access} retry={() => setRevision(value => value + 1)} />
          {access.data && <><ul className={drive.sharePeople}>{access.data.items.length ? access.data.items.map(person => <li key={person.id}><div className={drive.sharePerson}><span><strong>{person.name}</strong><span>{person.email}</span><small>{person.team_name}{person.status !== "active" ? ` / ${person.status}` : ""}</small>{person.email_status && emailLabels[person.email_status] && <small>{emailLabels[person.email_status]}</small>}</span><select className={drive.permissionSelect} aria-label={`Access for ${person.name}`} value={person.permission || "view"} disabled={busy || !link.data} onChange={event => void changePermission(person, event.target.value)}><option value="view">View</option><option value="edit" disabled={link.data?.public_access !== false}>Edit</option></select>{["failed", "cancelled"].includes(person.email_status || "") && <button type="button" className={shared.iconButton} disabled={busy} title={`Retry email to ${person.name}`} aria-label={`Retry email to ${person.name}`} onClick={() => void retryEmail(person)}><Mail size={17} /></button>}<button type="button" className={shared.iconButton} disabled={busy} title={`Remove access for ${person.name}`} aria-label={`Remove access for ${person.name}`} onClick={() => void remove(person)}><UserMinus size={17} /></button></div></li>) : <li className={drive.shareEmpty}>Not shared with anyone.</li>}</ul>{access.data.total > 10 && <Pagination total={access.data.total} page={page} setPage={setPage} />}</>}
        </section>
      </div>
      <footer className={shared.dialogFooter}><button type="button" className={shared.secondary} disabled={busy} onClick={onClose}>Done</button><button className={shared.primary} disabled={busy || !selected.length}><Share2 size={16} />{busy ? "Saving..." : `Share${selected.length ? ` (${selected.length})` : ""}`}</button></footer>
    </form>
  </dialog>;
}