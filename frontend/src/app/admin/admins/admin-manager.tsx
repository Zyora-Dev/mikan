"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight, Eye, EyeOff, LoaderCircle, Pencil, Plus, Search, Users, X } from "lucide-react";
import DeleteRecord from "../delete-record";
import styles from "../companies/companies.module.css";
import local from "./admins.module.css";

type Company = { id: number; name: string; email: string };
type CompanyAdmin = { id: number; name: string; email: string; mobile: string; company_id: number; company_name: string; is_active: boolean; created_at: string };
type Result = { items: CompanyAdmin[]; total: number };
type Fields = { name: string; email: string; mobile: string; password: string; company_id: string };
const empty: Fields = { name: "", email: "", mobile: "", password: "", company_id: "" };
const endpoint = "/api/admin/company-admins";
const createdDate = (value: string) => new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });

export default function AdminManager() {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [fields, setFields] = useState<Fields>(empty);
  const [editing, setEditing] = useState<CompanyAdmin | null>(null);
  const [active, setActive] = useState(true);
  const [visible, setVisible] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companiesLoading, setCompaniesLoading] = useState(false);
  const [companyError, setCompanyError] = useState("");
  const [companyRevision, setCompanyRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true); setError("");
      if (from && to && from > to) { setError("Start date must not be after end date."); setLoading(false); return; }
      const params = new URLSearchParams({ search: query, page: String(page), page_size: "10" });
      if (from) params.set("from_date", from);
      if (to) params.set("to_date", to);
      try {
        const response = await fetch(`${endpoint}?${params}`, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) });
        if (response.status === 401) { router.replace("/admin/login"); return; }
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Unable to load admins.");
        if (!controller.signal.aborted) setResult(data);
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to load admins.");
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [query, from, to, page, revision, router]);

  useEffect(() => {
    if (!formOpen) return;
    const controller = new AbortController();
    async function loadCompanies() {
      setCompaniesLoading(true); setCompanyError("");
      try {
        const items: Company[] = [];
        let companyPage = 1;
        while (!controller.signal.aborted) {
          const response = await fetch(`/api/admin/companies?page_size=100&page=${companyPage}`, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) });
          if (response.status === 401) { router.replace("/admin/login"); return; }
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail || "Unable to load companies.");
          items.push(...data.items);
          if (items.length >= data.total || !data.items.length) break;
          companyPage += 1;
        }
        if (!controller.signal.aborted) setCompanies(items.sort((first, second) => first.name.localeCompare(second.name)));
      } catch (failure) {
        if (!controller.signal.aborted) setCompanyError(failure instanceof Error ? failure.message : "Unable to load companies.");
      } finally { if (!controller.signal.aborted) setCompaniesLoading(false); }
    }
    void loadCompanies();
    return () => controller.abort();
  }, [formOpen, companyRevision, router]);

  function open(admin: CompanyAdmin | null = null) {
    setEditing(admin); setActive(admin?.is_active ?? true);
    setFields(admin ? { name: admin.name, email: admin.email, mobile: admin.mobile, company_id: String(admin.company_id), password: "" } : empty); setVisible(false); setFormError(""); setCompanies([]); setCompanyError("");
    setCompaniesLoading(true); setFormOpen(true);
    dialog.current?.showModal();
  }

  function clearFilters() {
    setSearch(""); setQuery(""); setFrom(""); setTo(""); setPage(1);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || companiesLoading || companyError || !fields.company_id) return;
    setSaving(true); setFormError("");
    try {
      const response = await fetch(editing ? `${endpoint}/${editing.id}` : endpoint, {
        method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...fields, password: editing && !fields.password ? null : fields.password, company_id: Number(fields.company_id), ...(editing ? { is_active: active } : {}) }), signal: AbortSignal.timeout(20000),
      });
      if (response.status === 401) { setFields(empty); router.replace("/admin/login"); return; }
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Unable to save admin.");
      setFields(empty); setVisible(false); dialog.current?.close();
      setNotice(`${data.name} ${editing ? "updated" : "created"} for ${data.company_name}.`);
      clearFilters(); setRevision(value => value + 1);
    } catch (failure) {
      setFormError(failure instanceof Error ? failure.message : "Unable to save admin.");
    } finally { setSaving(false); }
  }

  const pages = Math.max(1, Math.ceil((result?.total || 0) / 10));
  const filtered = Boolean(query || from || to);
  function actions(admin: CompanyAdmin) {
    return <div className={styles.rowActions}><button className={styles.iconButton} title={`Edit ${admin.name}`} aria-label={`Edit ${admin.name}`} onClick={() => open(admin)}><Pencil size={16} /></button><DeleteRecord name={admin.name} endpoint={`${endpoint}/${admin.id}`} onDeleted={() => { setNotice(`${admin.name} deleted.`); setPage(1); setRevision(value => value + 1); }} /></div>;
  }

  return <section className={styles.section} aria-labelledby="admins-title">
    <div className={styles.heading}><div><p className={styles.eyebrow}>ORGANIZATION</p><h1 id="admins-title">Admins</h1></div><button className={styles.primary} onClick={() => open()}><Plus size={17} />Create admin</button></div>
    {notice && <div className={styles.notice} role="status"><span>{notice}</span><button className={styles.iconButton} onClick={() => setNotice("")} aria-label="Dismiss notification" title="Dismiss notification"><X size={16} /></button></div>}
    <div className={styles.filters}>
      <form className={styles.search} onSubmit={event => { event.preventDefault(); setQuery(search.trim()); setPage(1); }}><Search size={17} /><input aria-label="Search admins" placeholder="Search name, email, mobile or company" maxLength={160} value={search} onChange={event => setSearch(event.target.value)} /><button className={styles.iconButton} aria-label="Search" title="Search"><ChevronRight size={18} /></button></form>
      <label className={styles.date}>Created from<input type="date" value={from} max={to || undefined} onChange={event => { setFrom(event.target.value); setPage(1); }} /></label>
      <label className={styles.date}>Created to<input type="date" value={to} min={from || undefined} onChange={event => { setTo(event.target.value); setPage(1); }} /></label>
      {filtered && <button className={styles.secondary} onClick={clearFilters}>Clear</button>}
    </div>
    <div aria-live="polite" aria-busy={loading}>
      {error ? <div className={styles.empty} role="alert"><p>{error}</p><button className={styles.secondary} onClick={() => setRevision(value => value + 1)}>Retry</button></div>
        : loading ? <div className={styles.skeleton} role="status" aria-label="Loading admins">{[0, 1, 2, 3].map(row => <div key={row}><span /><span /><span /></div>)}</div>
        : !result?.items.length ? <div className={styles.empty}><Users size={32} /><h2>{filtered ? "No matching admins" : "No admins yet"}</h2><button className={styles.primary} onClick={() => filtered ? clearFilters() : open()}>{filtered ? "Clear filters" : "Create admin"}</button></div>
        : <>
          <div className={styles.tableWrap}><table className={`${styles.table} ${local.adminTable}`}><thead><tr><th>Full name</th><th>Contact</th><th>Company</th><th>Created</th><th>Status</th><th>Actions</th></tr></thead><tbody>{result.items.map(admin => <tr key={admin.id}><td><strong>{admin.name}</strong></td><td><div className={styles.contact}><a href={`mailto:${admin.email}`}>{admin.email}</a><a href={`tel:${admin.mobile.replace(/[^+0-9]/g, "")}`}>{admin.mobile}</a></div></td><td>{admin.company_name}</td><td className={styles.created}>{createdDate(admin.created_at)}</td><td><span className={local.status}>{admin.is_active ? "Active" : "Inactive"}</span></td><td>{actions(admin)}</td></tr>)}</tbody></table></div>
          <div className={styles.mobileList}>{result.items.map(admin => <article className={styles.mobileItem} key={admin.id}><div className={styles.mobileHeading}><strong>{admin.name}</strong><span className={local.status}>{admin.is_active ? "Active" : "Inactive"}</span></div><dl><div><dt>Email</dt><dd><a href={`mailto:${admin.email}`}>{admin.email}</a></dd></div><div><dt>Mobile</dt><dd>{admin.mobile}</dd></div><div><dt>Company</dt><dd>{admin.company_name}</dd></div><div><dt>Created</dt><dd>{createdDate(admin.created_at)}</dd></div></dl>{actions(admin)}</article>)}</div>
        </>}
    </div>
    {!loading && !error && Boolean(result?.total) && <div className={styles.pagination}><span>{(page - 1) * 10 + 1}-{Math.min(page * 10, result!.total)} of {result!.total} admins</span><div><button className={styles.iconButton} disabled={page === 1} onClick={() => setPage(value => value - 1)} aria-label="Previous page" title="Previous page"><ChevronLeft size={18} /></button><span>Page {page} of {pages}</span><button className={styles.iconButton} disabled={page >= pages} onClick={() => setPage(value => value + 1)} aria-label="Next page" title="Next page"><ChevronRight size={18} /></button></div></div>}
    <dialog ref={dialog} className={styles.dialog} aria-labelledby="admin-dialog-title" onCancel={event => { if (saving) event.preventDefault(); }} onClose={() => { setFormOpen(false); setFields(empty); setVisible(false); }}>
      <form onSubmit={save}>
        <header className={styles.dialogHeader}><h2 id="admin-dialog-title">{editing ? "Edit admin" : "Create admin"}</h2><button type="button" className={styles.iconButton} onClick={() => dialog.current?.close()} disabled={saving} aria-label="Close" title="Close"><X size={20} /></button></header>
        <fieldset className={styles.formBody} disabled={saving}>
          <label className={styles.field}>Full name<input autoFocus required name="name" maxLength={160} value={fields.name} onChange={event => setFields({ ...fields, name: event.target.value })} autoComplete="name" /></label>
          <div className={styles.fieldGrid}><label className={styles.field}>Email<input required type="email" name="email" maxLength={254} value={fields.email} onChange={event => setFields({ ...fields, email: event.target.value })} autoComplete="email" /></label><label className={styles.field}>Mobile<input required type="tel" name="mobile" minLength={7} maxLength={25} value={fields.mobile} onChange={event => setFields({ ...fields, mobile: event.target.value })} autoComplete="tel" /></label></div>
          <div className={styles.field}><label htmlFor="admin-password">{editing ? "New password (optional)" : "Password"}</label><div className={local.password}><input id="admin-password" name="password" type={visible ? "text" : "password"} required={!editing} minLength={12} maxLength={128} autoComplete="new-password" placeholder={editing ? "Unchanged when blank" : "12-128 characters"} value={fields.password} onChange={event => setFields({ ...fields, password: event.target.value })} /><button type="button" className={styles.iconButton} onClick={() => setVisible(!visible)} aria-label={visible ? "Hide password" : "Show password"} title={visible ? "Hide password" : "Show password"}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></div>
          {editing && <label className={local.active}><input type="checkbox" checked={active} onChange={event => setActive(event.target.checked)} />Active account</label>}
          <label className={styles.field}>Select company<select className={local.companySelect} required name="company_id" value={fields.company_id} disabled={companiesLoading || !!companyError} onChange={event => setFields({ ...fields, company_id: event.target.value })}><option value="">{companiesLoading ? "Loading companies..." : "Select company"}</option>{companies.map(company => <option key={company.id} value={company.id}>{company.name} ({company.email})</option>)}</select></label>
          {companyError && <div className={local.companyState} role="alert"><span>{companyError}</span><button className={styles.secondary} type="button" onClick={() => setCompanyRevision(value => value + 1)}>Retry</button></div>}
          {!companiesLoading && !companyError && !companies.length && <p className={local.companyState}>No companies yet. <Link href="/admin/companies">Add company</Link></p>}
          {formError && <p className={styles.formError} role="alert">{formError}</p>}
        </fieldset>
        <footer className={styles.dialogFooter}><button type="button" className={styles.secondary} disabled={saving} onClick={() => dialog.current?.close()}>Cancel</button><button type="submit" className={styles.primary} disabled={saving || companiesLoading || !!companyError || !companies.length}>{saving && <LoaderCircle size={16} className={styles.spin} />}{saving ? "Saving..." : editing ? "Save changes" : "Create admin"}</button></footer>
      </form>
    </dialog>
  </section>;
}