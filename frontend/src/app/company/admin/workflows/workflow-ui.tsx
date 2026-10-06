"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import { teamRequest, TeamRequestError } from "@/lib/team-client";
import type { Person } from "@/lib/workflow";
import shared from "../../../../app/admin/companies/companies.module.css";
import styles from "./workflows.module.css";

export function useResource<Result>(path: string, company: boolean | "super", revision = 0) {
  const router = useRouter();
  const requestKey = JSON.stringify([path, company, revision]);
  const [state, setState] = useState<{ requestKey?: string; data?: Result; error: string; loading: boolean }>({ error: "", loading: true });
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setState({ requestKey, error: "", loading: true });
      try {
        const data = await teamRequest<Result>(path, undefined, controller.signal);
        if (!controller.signal.aborted) setState({ requestKey, data, error: "", loading: false });
      } catch (failure) {
        if (controller.signal.aborted) return;
        if (failure instanceof TeamRequestError && failure.status === 401) { router.replace(company === "super" ? "/admin/login" : company ? "/company/admin/login" : "/"); router.refresh(); }
        setState({ requestKey, error: failure instanceof Error ? failure.message : "Unable to load.", loading: false });
      }
    }
    void load();
    return () => controller.abort();
  }, [path, company, revision, router, requestKey]);
  return state.requestKey === requestKey ? state : { data: undefined, error: "", loading: true };
}

export function LoadState({ loading, error, retry }: { loading: boolean; error: string; retry: () => void }) {
  return loading ? <div className={shared.skeleton} role="status" aria-label="Loading">{[0, 1, 2].map(row => <div key={row}><span /><span /><span /></div>)}</div> : error ? <div className={shared.empty} role="alert"><p>{error}</p><button type="button" className={shared.secondary} onClick={retry}>Retry</button></div> : null;
}

export function Filters({ search, setSearch, from, setFrom, to, setTo }: { search: string; setSearch: (value: string) => void; from: string; setFrom: (value: string) => void; to: string; setTo: (value: string) => void }) {
  return <div className={shared.filters}>
    <label className={shared.search}><Search size={17} /><input aria-label="Search" placeholder="Search" maxLength={100} value={search} onChange={event => setSearch(event.target.value)} /></label>
    <label className={shared.date}>From date<input type="date" value={from} max={to || undefined} onChange={event => setFrom(event.target.value)} /></label>
    <label className={shared.date}>To date<input type="date" value={to} min={from || undefined} onChange={event => setTo(event.target.value)} /></label>
    {(search || from || to) && <button className={shared.secondary} onClick={() => { setSearch(""); setFrom(""); setTo(""); }}>Clear</button>}
  </div>;
}

export function Pagination({ total, page, setPage }: { total: number; page: number; setPage: (value: number) => void }) {
  return <div className={shared.pagination}><span>{total} records</span><div><button type="button" className={shared.iconButton} title="Previous page" aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(page - 1)}><ChevronLeft size={18} /></button><span>Page {page} of {Math.max(1, Math.ceil(total / 10))}</span><button type="button" className={shared.iconButton} title="Next page" aria-label="Next page" disabled={page * 10 >= total} onClick={() => setPage(page + 1)}><ChevronRight size={18} /></button></div></div>;
}

export function PeoplePicker({ url, company, selected, onChange, known = [], label, disabled = false }: { url: string; company: boolean; selected: number[]; onChange: (ids: number[]) => void; known?: Person[]; label: string; disabled?: boolean }) {
  const [search, setSearch] = useState("");
  const [remembered, setRemembered] = useState<Person[]>([]);
  const [open, setOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const state = useResource<{ items?: Person[]; people?: Person[] }>(`${url}${url.includes("?") ? "&" : "?"}search=${encodeURIComponent(search)}`, company, revision);
  const choices = state.data?.items || state.data?.people || [];
  const names = new Map([...known, ...remembered, ...choices].map(person => [person.id, person.name]));
  return <div className={styles.people}>
    <label className={styles.field}>{label}<input placeholder="Search people by name" value={search} maxLength={100} disabled={disabled} onFocus={() => setOpen(true)} onChange={event => { setSearch(event.target.value); setOpen(true); }} onKeyDown={event => { if (event.key === "Escape") setOpen(false); }} /></label>
    <div className={styles.chips}>{selected.map(identifier => <span className={styles.chip} key={identifier}>{names.get(identifier) || `Person #${identifier}`}<button type="button" title="Remove person" aria-label={`Remove ${names.get(identifier) || identifier}`} disabled={disabled} onClick={() => onChange(selected.filter(person => person !== identifier))}><X size={14} /></button></span>)}</div>
    {open && !disabled && <><div className={styles.actions}><span className={styles.sub}>{state.loading ? "Loading people..." : `${choices.length} matches`}</span><button type="button" className={shared.iconButton} aria-label="Close people search" title="Close search" onClick={() => setOpen(false)}><X size={16} /></button></div>{state.error ? <div role="alert"><p className={shared.formError}>{state.error}</p><button type="button" className={shared.secondary} onClick={() => setRevision(value => value + 1)}>Retry</button></div> : <ul className={styles.results}>{choices.filter(person => !selected.includes(person.id)).map(person => <li key={person.id}><button type="button" disabled={selected.length >= 25} onClick={() => { setRemembered(previous => [...previous.filter(item => item.id !== person.id), person]); onChange([...selected, person.id]); setSearch(""); }}>{person.name}</button></li>)}</ul>}</>}
  </div>;
}

export const dateTime = (value: string) => new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }).format(new Date(value)) + " IST";