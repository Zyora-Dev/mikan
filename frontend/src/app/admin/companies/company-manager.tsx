"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Building2, ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, Pencil, Plus, Search, Trash2, Upload, X } from "lucide-react";
import styles from "./companies.module.css";
import DeleteRecord from "../delete-record";

type Company = { id: number; name: string; mobile: string; email: string; website: string; address: string; has_logo: boolean; created_at: string; updated_at: string };
type Result = { items: Company[]; total: number; page: number; page_size: number };
type Fields = { name: string; mobile: string; email: string; website: string; address: string };
const empty: Fields = { name: "", mobile: "", email: "", website: "", address: "" };
const endpoint = "/api/admin/companies";
const logoUrl = (company: Company) => `${endpoint}/${company.id}/logo?v=${encodeURIComponent(company.updated_at)}`;

function CompanyLogo({ company }: { company: Company }) {
  const [failed, setFailed] = useState(false);
  return <span className={styles.logo}>{company.has_logo && !failed
    // eslint-disable-next-line @next/next/no-img-element
    ? <img src={logoUrl(company)} alt={`${company.name} logo`} onError={() => setFailed(true)} />
    : <Building2 size={22} />}</span>;
}

export default function CompanyManager() {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const upload = useRef<HTMLInputElement>(null);
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
  const [editing, setEditing] = useState<Company | null>(null);
  const [fields, setFields] = useState<Fields>(empty);
  const [preview, setPreview] = useState("");
  const [logo, setLogo] = useState<string | null>(null);
  const [removeLogo, setRemoveLogo] = useState(false);
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const fileSequence = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError("");
      if (from && to && from > to) {
        setError("Start date must not be after end date.");
        setLoading(false);
        return;
      }
      const params = new URLSearchParams({ search: query, page: String(page), page_size: "10" });
      if (from) params.set("from_date", from);
      if (to) params.set("to_date", to);
      try {
        const response = await fetch(`${endpoint}?${params}`, { signal: controller.signal, cache: "no-store" });
        if (response.status === 401) { router.replace("/admin/login"); return; }
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Unable to load companies.");
        if (!controller.signal.aborted) setResult(data);
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to load companies.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [query, from, to, page, revision, router]);

  function open(company: Company | null) {
    fileSequence.current += 1;
    setEditing(company);
    setFields(company ? { name: company.name, mobile: company.mobile, email: company.email, website: company.website, address: company.address } : empty);
    setPreview(company?.has_logo ? logoUrl(company) : "");
    setLogo(null);
    setRemoveLogo(false);
    setReading(false);
    setFormError("");
    if (upload.current) upload.current.value = "";
    dialog.current?.showModal();
  }

  function close() {
    if (saving) return;
    fileSequence.current += 1;
    dialog.current?.close();
  }

  async function selectLogo(file?: File) {
    if (!file) return;
    const sequence = ++fileSequence.current;
    setFormError("");
    const supported = ["image/png", "image/jpeg", "image/jpg", "image/webp"].includes(file.type.toLowerCase())
      || /\.(jpe?g|png|webp)$/i.test(file.name);
    if (!file.size || file.size > 2 * 1024 * 1024 || !supported) {
      setFormError(!file.size ? "The logo file is empty." : file.size > 2 * 1024 * 1024
        ? "The logo file exceeds 2 MB. Upload a smaller file."
        : "Choose a JPG, JPEG, PNG or WebP logo.");
      if (upload.current) upload.current.value = "";
      return;
    }
    setReading(true);
    try {
      const value = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("Unable to read this image."));
        reader.readAsDataURL(file);
      });
      if (sequence !== fileSequence.current) return;
      setLogo(value.slice(value.indexOf(",") + 1));
      setPreview(value);
      setRemoveLogo(false);
    } catch {
      if (sequence === fileSequence.current) setFormError("Unable to read this image. Choose another file.");
    } finally {
      if (sequence === fileSequence.current) setReading(false);
    }
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || reading) return;
    setSaving(true);
    setFormError("");
    try {
      const response = await fetch(editing ? `${endpoint}/${editing.id}` : endpoint, {
        method: editing ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...fields, logo_base64: logo, remove_logo: removeLogo }),
      });
      if (response.status === 401) { router.replace("/admin/login"); return; }
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Unable to save company.");
      dialog.current?.close();
      setNotice(`${data.name} ${editing ? "updated" : "created"}.`);
      setPage(1);
      setRevision(value => value + 1);
    } catch (failure) {
      setFormError(failure instanceof Error ? failure.message : "Unable to save company. Please try again.");
    } finally { setSaving(false); }
  }

  const pages = Math.max(1, Math.ceil((result?.total || 0) / 10));
  const filtered = Boolean(query || from || to);
  function actions(company: Company) {
    return <div className={styles.rowActions}><button className={styles.iconButton} onClick={() => open(company)} aria-label={`Edit ${company.name}`} title={`Edit ${company.name}`}><Pencil size={16} /></button><DeleteRecord name={company.name} endpoint={`${endpoint}/${company.id}`} onDeleted={() => { setNotice(`${company.name} deleted.`); setPage(1); setRevision(value => value + 1); }} /></div>;
  }

  return (
    <section className={styles.section} aria-labelledby="companies-title">
      <div className={styles.heading}><div><p className={styles.eyebrow}>ORGANIZATION</p><h1 id="companies-title">Companies</h1></div><button className={styles.primary} onClick={() => open(null)}><Plus size={17} />Add company</button></div>
      {notice && <div className={styles.notice} role="status"><span>{notice}</span><button type="button" className={styles.iconButton} onClick={() => setNotice("")} aria-label="Dismiss notification" title="Dismiss notification"><X size={16} /></button></div>}
      <div className={styles.filters}>
        <form className={styles.search} onSubmit={event => { event.preventDefault(); setQuery(search.trim()); setPage(1); }}><Search size={17} /><input aria-label="Search companies" placeholder="Search name, email or mobile" value={search} maxLength={160} onChange={event => setSearch(event.target.value)} /><button type="submit" className={styles.iconButton} aria-label="Search" title="Search"><ChevronRight size={18} /></button></form>
        <label className={styles.date}>Created from<input type="date" value={from} max={to || undefined} onChange={event => { setFrom(event.target.value); setPage(1); }} /></label>
        <label className={styles.date}>Created to<input type="date" value={to} min={from || undefined} onChange={event => { setTo(event.target.value); setPage(1); }} /></label>
        {filtered && <button className={styles.secondary} onClick={() => { setSearch(""); setQuery(""); setFrom(""); setTo(""); setPage(1); }}>Clear</button>}
      </div>
      <div aria-live="polite" aria-busy={loading}>
        {error ? <div className={styles.empty} role="alert"><p>{error}</p><button className={styles.secondary} onClick={() => setRevision(value => value + 1)}>Retry</button></div>
          : loading ? <div className={styles.skeleton} role="status" aria-label="Loading companies">{[0, 1, 2, 3].map(row => <div key={row}><span /><span /><span /></div>)}</div>
          : !result?.items.length ? <div className={styles.empty}><Building2 size={32} /><h2>{filtered ? "No matching companies" : "No companies yet"}</h2><button className={styles.primary} onClick={() => filtered ? (setSearch(""), setQuery(""), setFrom(""), setTo(""), setPage(1)) : open(null)}>{filtered ? "Clear filters" : "Add company"}</button></div>
          : <>
            <div className={styles.tableWrap}><table className={`${styles.table} ${styles.companyTable}`}><thead><tr><th>Company</th><th>Contact</th><th>Address</th><th>Created</th><th><span className={styles.srOnly}>Actions</span></th></tr></thead><tbody>{result.items.map(company => <tr key={company.id}>
              <td><div className={styles.company}><CompanyLogo key={company.updated_at} company={company} /><div><strong>{company.name}</strong>{company.website && <a href={company.website} target="_blank" rel="noopener noreferrer">{new URL(company.website).hostname}<ExternalLink size={12} /></a>}</div></div></td>
              <td><div className={styles.contact}><a href={`mailto:${company.email}`}>{company.email}</a><a href={`tel:${company.mobile.replace(/[^+0-9]/g, "")}`}>{company.mobile}</a></div></td>
              <td className={styles.address}>{company.address}</td><td className={styles.created}>{new Date(company.created_at).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" })}</td>
              <td>{actions(company)}</td>
            </tr>)}</tbody></table></div>
            <div className={styles.mobileList}>{result.items.map(company => <article key={company.id} className={styles.mobileItem}><div className={styles.mobileHeading}><div className={styles.company}><CompanyLogo key={company.updated_at} company={company} /><strong>{company.name}</strong></div>{actions(company)}</div><dl><div><dt>Email</dt><dd><a href={`mailto:${company.email}`}>{company.email}</a></dd></div><div><dt>Mobile</dt><dd>{company.mobile}</dd></div>{company.website && <div><dt>Website</dt><dd><a href={company.website} target="_blank" rel="noopener noreferrer">{company.website}<ExternalLink size={12} /></a></dd></div>}<div><dt>Address</dt><dd>{company.address}</dd></div><div><dt>Created</dt><dd>{new Date(company.created_at).toLocaleDateString("en-GB", { timeZone: "Asia/Kolkata" })}</dd></div></dl></article>)}</div>
          </>}
      </div>
      {!loading && !error && Boolean(result?.total) && <div className={styles.pagination}><span>{(page - 1) * 10 + 1}-{Math.min(page * 10, result!.total)} of {result!.total} companies</span><div><button className={styles.iconButton} disabled={page === 1} onClick={() => setPage(value => value - 1)} aria-label="Previous page" title="Previous page"><ChevronLeft size={18} /></button><span>Page {page} of {pages}</span><button className={styles.iconButton} disabled={page >= pages} onClick={() => setPage(value => value + 1)} aria-label="Next page" title="Next page"><ChevronRight size={18} /></button></div></div>}

      <dialog ref={dialog} className={styles.dialog} aria-labelledby="company-dialog-title" onCancel={event => { if (saving) event.preventDefault(); else fileSequence.current += 1; }}>
        <form onSubmit={save}>
          <header className={styles.dialogHeader}><h2 id="company-dialog-title">{editing ? "Edit company" : "Add company"}</h2><button type="button" className={styles.iconButton} onClick={close} disabled={saving} aria-label="Close" title="Close"><X size={20} /></button></header>
          <fieldset className={styles.formBody} disabled={saving}>
            <div className={styles.uploadRow}><span className={styles.preview}>{preview
              // eslint-disable-next-line @next/next/no-img-element
              ? <img src={preview} alt="Company logo preview" /> : <Building2 size={28} />}</span><div><label htmlFor="company-logo">Company logo <span className={styles.optional}>(optional)</span></label><div className={styles.uploadActions}><button type="button" className={styles.secondary} onClick={() => upload.current?.click()} disabled={reading}>{reading ? <LoaderCircle size={15} className={styles.spin} /> : <Upload size={15} />}{preview ? "Replace logo" : "Upload logo"}</button>{preview && <button type="button" className={styles.iconButton} title="Remove logo" aria-label="Remove logo" onClick={() => { fileSequence.current += 1; setReading(false); setLogo(null); setPreview(""); setRemoveLogo(true); if (upload.current) upload.current.value = ""; }}><Trash2 size={16} /></button>}</div><input ref={upload} id="company-logo" type="file" className={styles.srOnly} accept=".jpg,.jpeg,.png,.webp,image/png,image/jpeg,image/webp" onChange={event => void selectLogo(event.target.files?.[0])} /></div></div>
            <label className={styles.field}>Company name<input autoFocus name="name" required maxLength={160} value={fields.name} onChange={event => setFields({ ...fields, name: event.target.value })} autoComplete="organization" /></label>
            <div className={styles.fieldGrid}><label className={styles.field}>Mobile<input name="mobile" type="tel" required minLength={7} maxLength={25} value={fields.mobile} onChange={event => setFields({ ...fields, mobile: event.target.value })} autoComplete="tel" /></label><label className={styles.field}>Email<input name="email" type="email" required maxLength={254} value={fields.email} onChange={event => setFields({ ...fields, email: event.target.value })} autoComplete="email" /></label></div>
            <label className={styles.field}><span>Website <span className={styles.optional}>(optional)</span></span><input name="website" type="url" maxLength={2048} placeholder="https://" value={fields.website} onChange={event => setFields({ ...fields, website: event.target.value })} autoComplete="url" /></label>
            <label className={styles.field}>Address<textarea name="address" required rows={3} maxLength={2000} value={fields.address} onChange={event => setFields({ ...fields, address: event.target.value })} autoComplete="street-address" /></label>
            {formError && <p className={styles.formError} role="alert">{formError}</p>}
          </fieldset>
          <footer className={styles.dialogFooter}><button type="button" className={styles.secondary} onClick={close} disabled={saving}>Cancel</button><button className={styles.primary} type="submit" disabled={saving || reading}>{saving && <LoaderCircle size={16} className={styles.spin} />}{saving ? "Saving..." : editing ? "Save changes" : "Create company"}</button></footer>
        </form>
      </dialog>
    </section>
  );
}