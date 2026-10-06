"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Building2, ExternalLink, LoaderCircle, Pencil, Trash2, Upload, X } from "lucide-react";
import forms from "../../../admin/companies/companies.module.css";
import styles from "../dashboard.module.css";

type Fields = { name: string; mobile: string; email: string; website: string; address: string };
type Company = Fields & { id: number; has_logo: boolean; created_at: string; updated_at: string };
const endpoint = "/api/company/profile";
const logoUrl = (company: Company) => `${endpoint}/logo?v=${encodeURIComponent(company.updated_at)}`;

export default function CompanyProfile() {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const upload = useRef<HTMLInputElement>(null);
  const sequence = useRef(0);
  const [company, setCompany] = useState<Company | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState("");
  const [fields, setFields] = useState<Fields>({ name: "", mobile: "", email: "", website: "", address: "" });
  const [preview, setPreview] = useState("");
  const [logo, setLogo] = useState<string | null>(null);
  const [removeLogo, setRemoveLogo] = useState(false);
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [logoFailed, setLogoFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError("");
      try {
        const response = await fetch(endpoint, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) });
        if (response.status === 401) { router.replace("/company/admin/login"); router.refresh(); return; }
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Unable to load company details.");
        if (!controller.signal.aborted) { setCompany(data); setLogoFailed(false); }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to load company details.");
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [revision, router]);

  function open() {
    if (!company) return;
    sequence.current += 1;
    setFields({ name: company.name, mobile: company.mobile, email: company.email, website: company.website, address: company.address });
    setPreview(company.has_logo ? logoUrl(company) : "");
    setLogo(null); setRemoveLogo(false); setReading(false); setFormError("");
    if (upload.current) upload.current.value = "";
    dialog.current?.showModal();
  }

  function close() {
    if (saving) return;
    sequence.current += 1;
    dialog.current?.close();
  }

  async function selectLogo(file?: File) {
    if (!file) return;
    const current = ++sequence.current;
    setFormError("");
    const supported = ["image/png", "image/jpeg", "image/jpg", "image/webp"].includes(file.type.toLowerCase()) || /\.(jpe?g|png|webp)$/i.test(file.name);
    if (!file.size || file.size > 2 * 1024 * 1024 || !supported) {
      setReading(false);
      setFormError(!file.size ? "The logo file is empty." : file.size > 2 * 1024 * 1024 ? "The logo file exceeds 2 MB." : "Choose a JPG, JPEG, PNG or WebP logo.");
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
      if (current !== sequence.current) return;
      setLogo(value.slice(value.indexOf(",") + 1)); setPreview(value); setRemoveLogo(false);
    } catch {
      if (current === sequence.current) setFormError("Unable to read this image. Choose another file.");
    } finally { if (current === sequence.current) setReading(false); }
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || reading) return;
    setSaving(true); setFormError("");
    try {
      const response = await fetch(endpoint, {
        method: "PUT", headers: { "Content-Type": "application/json" }, signal: AbortSignal.timeout(20000),
        body: JSON.stringify({ ...fields, logo_base64: logo, remove_logo: removeLogo }),
      });
      if (response.status === 401) { router.replace("/company/admin/login"); router.refresh(); return; }
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Unable to save company details.");
      setCompany(data); setLogoFailed(false); setNotice("Company details updated.");
      dialog.current?.close(); router.refresh();
    } catch (failure) {
      setFormError(failure instanceof Error ? failure.message : "Unable to save company details. Please try again.");
    } finally { setSaving(false); }
  }

  return <section className={`${forms.section} ${styles.profileSection}`} aria-labelledby="company-title">
    <Link href="/company/admin" className={styles.backLink}><ArrowLeft size={15} />Dashboard</Link>
    <div className={forms.heading}><h1 id="company-title">Company details</h1><button className={forms.primary} onClick={open} disabled={!company || loading || Boolean(error)}><Pencil size={16} />Edit company</button></div>
    {notice && <div className={forms.notice} role="status"><span>{notice}</span><button className={forms.iconButton} onClick={() => setNotice("")} aria-label="Dismiss notification" title="Dismiss notification"><X size={16} /></button></div>}
    {loading ? <div className={forms.skeleton} role="status" aria-label="Loading company details">{[0, 1, 2].map(row => <div key={row}><span /><span /><span /></div>)}</div>
      : error ? <div className={forms.empty} role="alert"><p>{error}</p><button className={forms.secondary} onClick={() => setRevision(value => value + 1)}>Retry</button></div>
      : company && <>
        <div className={styles.companyProfileHeading}><span className={styles.companyProfileLogo}>{company.has_logo && !logoFailed ? <Image key={company.updated_at} src={logoUrl(company)} alt={`${company.name} logo`} width={88} height={88} unoptimized onError={() => setLogoFailed(true)} /> : <Building2 size={32} />}</span><div><h2>{company.name}</h2>{logoFailed && <p role="status">Logo could not be loaded.</p>}{company.website && <a href={company.website} target="_blank" rel="noopener noreferrer">{company.website}<ExternalLink size={14} /></a>}</div></div>
        <dl className={`${styles.details} ${styles.companyProfileDetails}`}>
          <div><dt>Company name</dt><dd>{company.name}</dd></div>
          <div><dt>Email address</dt><dd><a href={`mailto:${company.email}`}>{company.email}</a></dd></div>
          <div><dt>Mobile</dt><dd><a href={`tel:${company.mobile.replace(/[^+0-9]/g, "")}`}>{company.mobile}</a></dd></div>
          <div><dt>Website</dt><dd>{company.website ? <a href={company.website} target="_blank" rel="noopener noreferrer"><span>{company.website}</span><ExternalLink size={14} /></a> : "Not provided"}</dd></div>
          <div><dt>Address</dt><dd>{company.address}</dd></div>
        </dl>
      </>}
    <dialog ref={dialog} className={forms.dialog} aria-labelledby="edit-company-title" onCancel={event => { if (saving) event.preventDefault(); else sequence.current += 1; }}>
      <form onSubmit={save}>
        <header className={forms.dialogHeader}><h2 id="edit-company-title">Edit company</h2><button type="button" className={forms.iconButton} disabled={saving} onClick={close} title="Close" aria-label="Close"><X size={20} /></button></header>
        <fieldset className={forms.formBody} disabled={saving}>
          <div className={forms.uploadRow}><span className={forms.preview}>{preview ? <Image src={preview} alt="Company logo preview" width={72} height={72} unoptimized /> : <Building2 size={28} />}</span><div><label htmlFor="profile-logo">Company logo <span className={forms.optional}>(optional)</span></label><div className={forms.uploadActions}><button type="button" className={forms.secondary} onClick={() => upload.current?.click()} disabled={reading}>{reading ? <LoaderCircle size={15} className={forms.spin} /> : <Upload size={15} />}{preview ? "Replace logo" : "Upload logo"}</button>{preview && <button type="button" className={forms.iconButton} title="Remove logo" aria-label="Remove logo" onClick={() => { sequence.current += 1; setReading(false); setLogo(null); setPreview(""); setRemoveLogo(true); if (upload.current) upload.current.value = ""; }}><Trash2 size={16} /></button>}</div><input ref={upload} id="profile-logo" type="file" className={forms.srOnly} accept=".jpg,.jpeg,.png,.webp,image/png,image/jpeg,image/webp" onChange={event => void selectLogo(event.target.files?.[0])} /></div></div>
          <label className={forms.field}>Company name<input name="name" autoFocus required maxLength={160} autoComplete="organization" value={fields.name} onChange={event => setFields({ ...fields, name: event.target.value })} /></label>
          <div className={forms.fieldGrid}><label className={forms.field}>Mobile<input name="mobile" type="tel" required minLength={7} maxLength={25} autoComplete="tel" value={fields.mobile} onChange={event => setFields({ ...fields, mobile: event.target.value })} /></label><label className={forms.field}>Email<input name="email" type="email" required maxLength={254} autoComplete="email" value={fields.email} onChange={event => setFields({ ...fields, email: event.target.value })} /></label></div>
          <label className={forms.field}><span>Website <span className={forms.optional}>(optional)</span></span><input name="website" type="url" maxLength={2048} placeholder="https://" autoComplete="url" value={fields.website} onChange={event => setFields({ ...fields, website: event.target.value })} /></label>
          <label className={forms.field}>Address<textarea name="address" required rows={3} maxLength={2000} autoComplete="street-address" value={fields.address} onChange={event => setFields({ ...fields, address: event.target.value })} /></label>
          {formError && <p className={forms.formError} role="alert">{formError}</p>}
        </fieldset>
        <footer className={forms.dialogFooter}><button type="button" className={forms.secondary} disabled={saving} onClick={close}>Cancel</button><button type="submit" className={forms.primary} disabled={saving || reading}>{saving && <LoaderCircle size={16} className={forms.spin} />}{saving ? "Saving..." : "Save changes"}</button></footer>
      </form>
    </dialog>
  </section>;
}