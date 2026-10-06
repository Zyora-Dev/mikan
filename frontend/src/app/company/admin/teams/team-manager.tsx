"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Ban, ChevronLeft, ChevronRight, LoaderCircle, MailPlus, Pencil, Plus, RefreshCw, Search, ShieldCheck, Users, X } from "lucide-react";
import { teamRequest, TeamRequestError } from "@/lib/team-client";
import styles from "../../../admin/companies/companies.module.css";
import local from "./teams.module.css";
import DeleteRecord from "../../../admin/delete-record";

type Team = { id: number; name: string; created_at: string; people: number; manager: string | null; manager_id: number | null; manager_status: string | null };
type Person = { id: number; name: string; email: string; mobile: string; role: "manager" | "member"; team_id: number; team_name: string; status: "invited" | "active" | "disabled"; auth_type: string | null; invitation_expires_at: string | null; created_at: string };
type List<Result> = { items: Result[]; total: number; summary?: { active: number; invited: number } };
const empty = { name: "", email: "", mobile: "", role: "member", team_id: "" };
const dateText = (value: string) => new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
const roleText = (value: string) => value === "manager" ? "Team Manager" : "Member";
const stateText = (person: Person) => person.status === "invited" && person.invitation_expires_at && new Date(person.invitation_expires_at).getTime() < Date.now() ? "expired" : person.status;

export default function TeamManager({ endpoint = "/api/company/teams", superAdmin = false }: { endpoint?: string; superAdmin?: boolean }) {
  const loginPath = superAdmin ? "/admin/login" : "/company/admin/login";
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const confirmation = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<"teams" | "people">(superAdmin ? "people" : "teams");
  const [teams, setTeams] = useState<Team[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<{ active: number; invited: number }>();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mode, setMode] = useState<"team" | "invite" | "edit" | null>(null);
  const [editing, setEditing] = useState<Person | null>(null);
  const [fields, setFields] = useState(empty);
  const [choices, setChoices] = useState<Team[]>([]);
  const [choicesLoading, setChoicesLoading] = useState(false);
  const [choicesError, setChoicesError] = useState("");
  const [choicesRevision, setChoicesRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [target, setTarget] = useState<Person | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    function syncTab() {
      const next = window.location.hash === "#members" || (superAdmin && window.location.hash !== "#teams") ? "people" : "teams";
      if (!["#teams", "#members"].includes(window.location.hash)) {
        window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}${superAdmin ? "#members" : "#teams"}`);
      }
      setTab(next); setSearch(""); setQuery(""); setFrom(""); setTo(""); setPage(1);
    }
    syncTab();
    window.addEventListener("hashchange", syncTab);
    return () => window.removeEventListener("hashchange", syncTab);
  }, [superAdmin]);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true); setError("");
      if (from && to && from > to) { setError("Start date must not be after end date."); setLoading(false); return; }
      const params = new URLSearchParams({ page: String(page), page_size: "10", search: query });
      if (from) params.set("from_date", from);
      if (to) params.set("to_date", to);
      try {
        if (tab === "teams") {
          const result = await teamRequest<List<Team>>(`${endpoint}?${params}`, undefined, controller.signal);
          if (!controller.signal.aborted) { setTeams(result.items); setTotal(result.total); }
        } else {
          const result = await teamRequest<List<Person>>(`${endpoint}/people?${params}`, undefined, controller.signal);
          if (!controller.signal.aborted) { setPeople(result.items); setTotal(result.total); setSummary(result.summary); }
        }
      } catch (failure) {
        if (!controller.signal.aborted) {
          if (failure instanceof TeamRequestError && failure.status === 401) router.replace(loginPath);
          setError(failure instanceof Error ? failure.message : "Unable to load teams.");
        }
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [tab, query, from, to, page, revision, router, endpoint, loginPath]);

  useEffect(() => {
    if (mode !== "invite" && mode !== "edit") return;
    const controller = new AbortController();
    async function loadChoices() {
      setChoicesLoading(true); setChoicesError("");
      try {
        const all: Team[] = [];
        let currentPage = 1;
        while (!controller.signal.aborted) {
          const result = await teamRequest<List<Team>>(`${endpoint}?page_size=100&page=${currentPage}`, undefined, controller.signal);
          all.push(...result.items);
          if (all.length >= result.total || !result.items.length) break;
          currentPage += 1;
        }
        if (!controller.signal.aborted) setChoices(all.sort((first, second) => first.name.localeCompare(second.name)));
      } catch (failure) {
        if (!controller.signal.aborted) {
          if (failure instanceof TeamRequestError && failure.status === 401) router.replace(loginPath);
          setChoicesError(failure instanceof Error ? failure.message : "Unable to load teams.");
        }
      } finally { if (!controller.signal.aborted) setChoicesLoading(false); }
    }
    void loadChoices();
    return () => controller.abort();
  }, [mode, choicesRevision, router, endpoint, loginPath]);

  function open(next: "team" | "invite" | "edit", teamId?: number) {
    setEditing(null);
    setFields({ ...empty, team_id: teamId ? String(teamId) : "" }); setFormError(""); setChoicesError(""); setChoices([]);
    setChoicesLoading(next !== "team"); setMode(next); dialog.current?.showModal();
  }
  function edit(person: Person) {
    open("edit", person.team_id);
    setEditing(person);
    setFields({ name: person.name, email: person.email, mobile: person.mobile, role: person.role, team_id: String(person.team_id) });
  }
  function handleDialogClose() {
    if (!dialog.current?.open) { setMode(null); setFields(empty); setEditing(null); }
  }
  function clear() { setSearch(""); setQuery(""); setFrom(""); setTo(""); setPage(1); }
  function changeTab(next: "teams" | "people") { window.location.hash = next === "teams" ? "teams" : "members"; }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    setSaving(true); setFormError("");
    try {
      const path = mode === "team" ? endpoint : mode === "edit" ? `${endpoint}/people/${editing?.id}/edit` : `${endpoint}/invite`;
      const result = await teamRequest<{ name?: string; detail?: string }>(path, mode === "team" ? { name: fields.name } : { ...fields, team_id: Number(fields.team_id) });
      setNotice(result.detail || `Team ${result.name} created.`); dialog.current?.close();
      changeTab(mode === "team" ? "teams" : "people");
      clear(); setRevision(value => value + 1); router.refresh();
    } catch (failure) {
      if (failure instanceof TeamRequestError && failure.status === 401) router.replace(loginPath);
      setFormError(failure instanceof Error ? failure.message : "Unable to save.");
      if (failure instanceof TeamRequestError && failure.status === 409) setChoicesRevision(value => value + 1);
    } finally { setSaving(false); }
  }

  async function action(person: Person, actionName: "resend" | "disable") {
    if (busy !== null) return;
    setBusy(person.id); setError(""); setFormError("");
    try {
      const result = await teamRequest<{ detail: string }>(`${endpoint}/people/${person.id}/${actionName}`, {});
      setNotice(result.detail); confirmation.current?.close(); setRevision(value => value + 1); router.refresh();
    } catch (failure) {
      if (failure instanceof TeamRequestError && failure.status === 401) router.replace(loginPath);
      const detail = failure instanceof Error ? failure.message : "Unable to update access.";
      if (actionName === "disable") setFormError(detail); else setError(detail);
    } finally { setBusy(null); }
  }

  function personActions(person: Person) {
    return <div className={local.rowActions}>
      {superAdmin && <DeleteRecord name={person.name} endpoint={`${endpoint}/people/${person.id}/delete`} method="POST" onDeleted={() => { setPage(1); setNotice(`${person.name} deleted.`); setRevision(value => value + 1); }} />}
      {person.status !== "disabled" && <button className={styles.iconButton} disabled={busy !== null} onClick={() => edit(person)} aria-label={`Edit ${person.name}`} title="Edit member"><Pencil size={17} /></button>}
      {person.status === "invited" && <button className={styles.iconButton} disabled={busy !== null} onClick={() => void action(person, "resend")} aria-label={`Resend invitation to ${person.name}`} title="Resend invitation">{busy === person.id ? <LoaderCircle size={17} className={styles.spin} /> : <RefreshCw size={17} />}</button>}
      {person.status !== "disabled" && <button className={`${styles.iconButton} ${local.revokeAction}`} disabled={busy !== null} onClick={() => { setTarget(person); setFormError(""); confirmation.current?.showModal(); }} aria-label={`Revoke access for ${person.name}`} title={person.status === "invited" ? "Cancel invitation" : "Revoke access"}><Ban size={17} /></button>}
    </div>;
  }
  const assigned = choices.find(team => String(team.id) === fields.team_id);
  const managerOccupied = Boolean(assigned?.manager_id && assigned.manager_id !== editing?.id);
  const filtered = Boolean(query || from || to);
  const pages = Math.max(1, Math.ceil(total / 10));

  return <section className={styles.section} aria-labelledby="teams-heading">
    <div className={`${styles.heading} ${local.heading}`}><div><p className={styles.eyebrow}>{superAdmin ? "ORGANIZATION" : "COMPANY WORKSPACE"}</p><h1 id="teams-heading">{superAdmin ? "Members" : "Teams"}</h1></div><div className={local.actions}><button className={styles.secondary} onClick={() => open("team")}><Plus size={17} />Create team</button><button className={styles.primary} onClick={() => open("invite")}><MailPlus size={17} />Invite to Mikan Cloud</button></div></div>
    {notice && <div className={styles.notice} role="status"><span>{notice}</span><button className={styles.iconButton} onClick={() => setNotice("")} aria-label="Dismiss" title="Dismiss"><X size={17} /></button></div>}
    <nav className={local.tabs} aria-label="Team views"><a href="#teams" aria-current={tab === "teams" ? "page" : undefined}>Teams</a><a href="#members" aria-current={tab === "people" ? "page" : undefined}>Members</a></nav>
    {tab === "people" && summary && <div className={local.summary}><span><strong>{summary.active}</strong> active people</span><span><strong>{summary.invited}</strong> pending invitations</span></div>}
    <div className={styles.filters}><form className={styles.search} onSubmit={event => { event.preventDefault(); setQuery(search.trim()); setPage(1); }}><Search size={17} /><input aria-label="Search teams or people" placeholder={tab === "teams" ? "Search teams" : "Search name, email, mobile or team"} maxLength={160} value={search} onChange={event => setSearch(event.target.value)} /><button className={styles.iconButton} title="Search" aria-label="Search"><ChevronRight size={18} /></button></form><label className={styles.date}>Created from<input type="date" value={from} max={to || undefined} onChange={event => { setFrom(event.target.value); setPage(1); }} /></label><label className={styles.date}>Created to<input type="date" value={to} min={from || undefined} onChange={event => { setTo(event.target.value); setPage(1); }} /></label>{filtered && <button className={styles.secondary} onClick={clear}>Clear</button>}</div>
    <div aria-live="polite" aria-busy={loading}>
      {error ? <div className={styles.empty} role="alert"><p>{error}</p><button className={styles.secondary} onClick={() => setRevision(value => value + 1)}>Retry</button></div> : loading ? <div className={styles.skeleton} role="status" aria-label="Loading teams">{[0, 1, 2, 3].map(row => <div key={row}><span /><span /><span /></div>)}</div> : !total ? <div className={styles.empty}><Users size={32} /><h2>{filtered ? "No matches" : tab === "teams" ? "No teams yet" : "No invitations yet"}</h2><button className={styles.primary} onClick={filtered ? clear : () => open(tab === "teams" ? "team" : "invite")}>{filtered ? "Clear filters" : tab === "teams" ? "Create team" : "Invite to Mikan Cloud"}</button></div> : tab === "teams" ? <>
        <div className={styles.tableWrap}><table className={`${styles.table} ${local.table}`}><thead><tr><th>Team</th><th>Team Manager</th><th>People</th><th>Created</th><th>Invite</th></tr></thead><tbody>{teams.map(team => <tr key={team.id}><td><strong>{team.name}</strong></td><td>{team.manager || "Not assigned"}{team.manager_status === "invited" && <span className={local.sub}>Invitation pending</span>}</td><td>{team.people}</td><td>{dateText(team.created_at)}</td><td><button className={styles.iconButton} title={`Invite to ${team.name}`} aria-label={`Invite to ${team.name}`} onClick={() => open("invite", team.id)}><MailPlus size={18} /></button></td></tr>)}</tbody></table></div>
        <div className={styles.mobileList}>{teams.map(team => <article key={team.id} className={styles.mobileItem}><div className={styles.mobileHeading}><strong>{team.name}</strong><button className={styles.iconButton} title={`Invite to ${team.name}`} aria-label={`Invite to ${team.name}`} onClick={() => open("invite", team.id)}><MailPlus size={18} /></button></div><dl><div><dt>Manager</dt><dd>{team.manager || "Not assigned"}{team.manager_status === "invited" && " (invited)"}</dd></div><div><dt>People</dt><dd>{team.people}</dd></div><div><dt>Created</dt><dd>{dateText(team.created_at)}</dd></div></dl></article>)}</div>
      </> : <>
        <div className={`${styles.tableWrap} ${local.membersWrap}`} tabIndex={0} role="region" aria-label="Members table">
          <table className={`${styles.table} ${local.membersTable} ${superAdmin ? local.superMembers : ""}`}>
            <thead><tr><th scope="col">Member</th><th scope="col">Contact</th><th scope="col">Team</th><th scope="col">Status</th><th scope="col">Added on</th><th scope="col">Actions</th></tr></thead>
            <tbody>{people.map(person => <tr key={person.id}>
              <td><strong className={local.memberName}>{person.name}</strong><span className={local.memberRole}>{person.role === "manager" && <ShieldCheck size={14} aria-hidden="true" />}{roleText(person.role)}</span></td>
              <td><div className={local.memberContact}><a href={`mailto:${person.email}`}>{person.email}</a><a href={`tel:${person.mobile}`}>{person.mobile}</a></div></td>
              <td><span className={local.memberTeam}>{person.team_name}</span></td>
              <td><span className={local.status} data-status={stateText(person)}>{stateText(person)}</span><span className={local.sub}>{person.auth_type === "otp" ? "Email OTP" : person.auth_type === "password" ? "Password" : "Not activated"}</span></td>
              <td><time className={local.memberDate} dateTime={person.created_at}>{dateText(person.created_at)}</time></td>
              <td>{personActions(person)}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <div className={styles.mobileList}>{people.map(person => <article key={person.id} className={`${styles.mobileItem} ${local.memberItem}`}>
          <div className={local.memberHeader}><div><strong className={local.memberName}>{person.name}</strong><span className={local.memberRole}>{roleText(person.role)}</span></div><span className={local.status} data-status={stateText(person)}>{stateText(person)}</span></div>
          <dl><div><dt>Team</dt><dd>{person.team_name}</dd></div><div><dt>Email</dt><dd><a href={`mailto:${person.email}`}>{person.email}</a></dd></div><div><dt>Mobile</dt><dd><a href={`tel:${person.mobile}`}>{person.mobile}</a></dd></div><div><dt>Sign-in</dt><dd>{person.auth_type === "otp" ? "Email OTP" : person.auth_type === "password" ? "Password" : "Not activated"}</dd></div></dl>
          <footer className={local.memberFooter}><span>Added {dateText(person.created_at)}</span>{personActions(person)}</footer>
        </article>)}</div>
      </>}
    </div>
    {!loading && !error && total > 0 && <div className={styles.pagination}><span>{(page - 1) * 10 + 1}-{Math.min(page * 10, total)} of {total}</span><div><button className={styles.iconButton} disabled={page === 1} onClick={() => setPage(value => value - 1)} aria-label="Previous page" title="Previous page"><ChevronLeft size={18} /></button><span>Page {page} of {pages}</span><button className={styles.iconButton} disabled={page >= pages} onClick={() => setPage(value => value + 1)} aria-label="Next page" title="Next page"><ChevronRight size={18} /></button></div></div>}
    <dialog ref={dialog} className={styles.dialog} aria-labelledby="team-dialog-title" onCancel={event => { if (saving) event.preventDefault(); }} onClose={handleDialogClose}><form onSubmit={save}><header className={styles.dialogHeader}><h2 id="team-dialog-title">{mode === "team" ? "Create team" : mode === "edit" ? "Edit member" : "Invite to Mikan Cloud"}</h2><button type="button" className={styles.iconButton} disabled={saving} onClick={() => dialog.current?.close()} title="Close" aria-label="Close"><X size={20} /></button></header><fieldset className={styles.formBody} disabled={saving}>
      <label className={styles.field}>{mode === "team" ? "Team name" : "Full name"}<input autoFocus required maxLength={mode === "team" ? 100 : 120} autoComplete={mode === "team" ? "off" : "name"} value={fields.name} onChange={event => setFields({ ...fields, name: event.target.value })} /></label>
      {(mode === "invite" || mode === "edit") && <>
        <div className={styles.fieldGrid}>
          <label className={styles.field}>Email<input required type="email" maxLength={254} autoComplete="email" value={fields.email} onChange={event => setFields({ ...fields, email: event.target.value })} /></label>
          <label className={styles.field}>Mobile<input required type="tel" minLength={7} maxLength={25} autoComplete="tel" value={fields.mobile} onChange={event => setFields({ ...fields, mobile: event.target.value })} /></label>
        </div>
        {editing && fields.email.trim().toLowerCase() !== editing.email.toLowerCase() && <p className={local.assigned}>Changing email ends existing access and sends a new activation link. The member must verify the new address and select their sign-in method again.</p>}
        <label className={styles.field}>Team<select className={local.select} required value={fields.team_id} disabled={choicesLoading || !!choicesError} onChange={event => setFields({ ...fields, team_id: event.target.value, role: "member" })}><option value="">{choicesLoading ? "Loading teams..." : "Select team"}</option>{choices.map(team => <option key={team.id} value={team.id}>{team.name}</option>)}</select></label>
        {assigned && <p className={local.assigned}><ShieldCheck size={18} /><span>Team Manager: <strong>{assigned.manager || "Not assigned"}</strong>{assigned.manager_status === "invited" && " (invitation pending)"}</span></p>}
        <label className={styles.field}>Role<select className={local.select} value={fields.role} onChange={event => setFields({ ...fields, role: event.target.value })}><option value="member">Member</option><option value="manager" disabled={managerOccupied}>Team Manager{managerOccupied ? " (already assigned)" : ""}</option></select></label>
        {choicesError && <div role="alert"><p className={styles.formError}>{choicesError}</p><button type="button" className={styles.secondary} onClick={() => setChoicesRevision(value => value + 1)}>Retry</button></div>}
        {!choicesLoading && !choicesError && !choices.length && <p>No teams yet. <button type="button" className={styles.secondary} onClick={() => { setMode("team"); setFields(empty); setEditing(null); setFormError(""); }}>Create team</button></p>}
      </>}
      {formError && <p className={styles.formError} role="alert">{formError}</p>}</fieldset><footer className={styles.dialogFooter}><button type="button" className={styles.secondary} disabled={saving} onClick={() => dialog.current?.close()}>Cancel</button><button className={styles.primary} disabled={saving || ((mode === "invite" || mode === "edit") && (choicesLoading || !!choicesError || !fields.team_id || (fields.role === "manager" && managerOccupied)))}>{saving && <LoaderCircle size={16} className={styles.spin} />}{saving ? "Saving..." : mode === "team" ? "Create team" : mode === "edit" ? "Save changes" : "Send invitation"}</button></footer></form></dialog>
    <dialog ref={confirmation} className={styles.dialog} aria-labelledby="revoke-title" onCancel={event => { if (busy !== null) event.preventDefault(); }} onClose={() => setTarget(null)}><header className={styles.dialogHeader}><h2 id="revoke-title">{target?.status === "invited" ? "Cancel invitation?" : "Revoke access?"}</h2></header><div className={styles.formBody}><p className={local.confirm}>{target?.name} will lose access to Mikan Cloud. Existing sessions and verification links will be cancelled.</p>{formError && <p className={styles.formError} role="alert">{formError}</p>}</div><footer className={styles.dialogFooter}><button className={styles.secondary} disabled={busy !== null} onClick={() => confirmation.current?.close()}>Keep access</button><button className={styles.primary} disabled={busy !== null} onClick={() => target && void action(target, "disable")}>{busy !== null ? "Revoking..." : "Revoke access"}</button></footer></dialog>
  </section>;
}