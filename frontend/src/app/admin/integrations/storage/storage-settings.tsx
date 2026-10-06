"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, ArrowUpRight, Check, ChevronLeft, ChevronRight, Cloud, Database, Eye, EyeOff, HardDrive, LoaderCircle, Pencil, Plus, RefreshCw, Save, Search, Settings2, ShieldCheck, Trash2, X } from "lucide-react";
import styles from "../integrations.module.css";

type Provider = "r2" | "s3";
type Connection = {
  id: number; provider: Provider; name: string; bucket: string; region: string;
  account_id: string | null; endpoint: string; enabled: boolean;
  created_at: string; updated_at: string; last_test_at: string | null;
  last_test_status: "passed" | "failed" | "cleanup_failed" | null;
  last_cleanup: { key: string; bucket: string; endpoint: string; reported_at: string } | null;
};
type Listing = { items: Connection[]; total: number; regions: string[] };
type Summary = { items: { provider: Provider; total: number; enabled: number }[] };
type Fields = { name: string; bucket: string; region: string; account_id: string; endpoint: string; access_key: string; secret_key: string; enabled: boolean };
const providers = {
  r2: { name: "Cloudflare R2", company: "Cloudflare", icon: Cloud, iconClass: "", api: "S3-compatible", docs: "https://developers.cloudflare.com/r2/api/tokens/" },
  s3: { name: "AWS S3", company: "Amazon Web Services", icon: Database, iconClass: styles.awsIcon, api: "Amazon S3", docs: "https://docs.aws.amazon.com/AmazonS3/latest/userguide/security_iam_service-with-iam.html" },
};
const empty = (provider: Provider): Fields => ({ name: "", bucket: "", region: provider === "r2" ? "auto" : "", account_id: "", endpoint: "", access_key: "", secret_key: "", enabled: false });
const dateText = (value: string) => new Date(value).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }) + " IST";
const message = (reason: unknown) => reason instanceof Error ? reason.message : "Unable to complete the request.";

async function request(router: ReturnType<typeof useRouter>, path = "", options?: RequestInit) {
  const response = await fetch("/api/admin/integrations/storage" + path, { ...options, cache: "no-store", headers: { "Content-Type": "application/json" }, signal: options?.signal ?? AbortSignal.timeout(65000) });
  if (response.status === 401) { router.replace("/admin/login"); router.refresh(); throw new Error("Sign in required."); }
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Unable to complete the request.");
  return result;
}

export function StorageCards({ overview = false }: { overview?: boolean }) {
  const router = useRouter();
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    request(router, "", { signal: controller.signal }).then(value => { setData(value); setError(""); })
      .catch(reason => { if (!controller.signal.aborted) setError(message(reason)); });
    return () => controller.abort();
  }, [router, revision]);
  const cards = overview ? ["storage" as const] : ["r2" as const, "s3" as const];
  return <>{cards.map(key => {
    const config = key === "storage" ? { name: "Storage", company: "Cloudflare R2 & AWS S3", icon: HardDrive, iconClass: styles.storageIcon, api: "R2, S3" } : providers[key];
    const counts = data?.items.filter(item => key === "storage" || item.provider === key);
    const total = counts?.reduce((sum, item) => sum + item.total, 0) ?? 0;
    const enabled = counts?.reduce((sum, item) => sum + item.enabled, 0) ?? 0;
    return <article className={styles.card} key={key}>
      <div className={styles.cardTop}><span className={`${styles.providerIcon} ${config.iconClass}`}><config.icon size={27} /></span><span className={data && enabled ? styles.activeBadge : styles.badge}>{error ? "Unavailable" : !data ? "Loading" : total ? "Configured" : "Not configured"}</span></div>
      <h2>{config.name}</h2><p className={styles.provider}>{config.company}</p>
      <dl className={styles.summary}><div><dt>API</dt><dd>{config.api}</dd></div><div><dt>Connections</dt><dd>{data ? total : "Unavailable"}</dd></div><div><dt>Enabled</dt><dd>{data ? enabled : "Unavailable"}</dd></div></dl>
      {error && <div className={styles.error} role="alert">{error}<button className={styles.iconButton} title="Retry loading storage" aria-label="Retry loading storage" onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} /></button></div>}
      <Link href={key === "storage" ? "/admin/integrations/storage" : `/admin/integrations/storage/${key}`} className={styles.cardAction}><Settings2 size={16} />{key === "storage" ? "View providers" : "Configure"}<ArrowRight size={17} /></Link>
    </article>;
  })}</>;
}

export default function StorageSettings({ provider }: { provider: Provider }) {
  const router = useRouter();
  const config = providers[provider];
  const [data, setData] = useState<Listing | null>(null);
  const [filters, setFilters] = useState({ search: "", from_date: "", to_date: "" });
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const [editing, setEditing] = useState<Connection | null>(null);
  const [fields, setFields] = useState<Fields>(empty(provider));
  const [visible, setVisible] = useState(false);
  const [action, setAction] = useState<{ type: "test" | "remove"; item: Connection } | null>(null);
  const formDialog = useRef<HTMLDialogElement>(null);
  const confirmDialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    request(router, `/${provider}?${query}&page=${page}`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setData(value); setError(""); }
    }).catch(reason => { if (!controller.signal.aborted) setError(message(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [provider, page, query, revision, router]);

  function refresh() { setLoading(true); setRevision(value => value + 1); }
  function openForm(item: Connection | null) {
    setEditing(item); setVisible(false); setFormError("");
    setFields(item ? { name: item.name, bucket: item.bucket, region: item.region, account_id: item.account_id ?? "", endpoint: provider === "r2" ? item.endpoint : "", access_key: "", secret_key: "", enabled: item.enabled } : empty(provider));
    formDialog.current?.showModal();
  }
  function closeForm() { formDialog.current?.close(); setFields(empty(provider)); setVisible(false); setEditing(null); }
  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLoading(true); setPage(1); setQuery(new URLSearchParams(Object.entries(filters).filter(([, value]) => value)).toString()); setRevision(value => value + 1);
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setFormError("");
    try {
      await request(router, `/${provider}${editing ? `/${editing.id}` : ""}`, { method: editing ? "PUT" : "POST", body: JSON.stringify({ ...fields, account_id: provider === "r2" ? fields.account_id : null, endpoint: provider === "r2" ? fields.endpoint || null : null, access_key: fields.access_key || null, secret_key: fields.secret_key || null }) });
      closeForm(); setNotice("Storage connection saved."); refresh();
    } catch (reason) { setFormError(message(reason)); }
    finally { setPending(false); }
  }
  function confirm(type: "test" | "remove", item: Connection) {
    setAction({ type, item }); setFormError(""); confirmDialog.current?.showModal();
  }
  async function runAction() {
    if (!action) return;
    setPending(true); setFormError(""); setNotice("");
    try {
      const result = await request(router, `/${provider}/${action.item.id}${action.type === "test" ? "/test" : ""}`, { method: action.type === "test" ? "POST" : "DELETE", body: action.type === "test" ? JSON.stringify({ confirm: true }) : "{}" });
      if (result.status && result.status !== "passed") {
        setFormError(result.detail + (result.cleanup_key ? ` Object key: ${result.cleanup_key}` : ""));
      } else { setNotice(result.detail); confirmDialog.current?.close(); setAction(null); }
      if (action.type === "remove" && data?.items.length === 1 && page > 1) setPage(value => value - 1);
      refresh();
    } catch (reason) { setFormError(message(reason)); }
    finally { setPending(false); }
  }

  const hasFilters = Boolean(query);
  const endpoint = provider === "s3" ? fields.region ? `https://s3.${fields.region}.amazonaws.com` : "" : fields.endpoint || (fields.account_id ? `https://${fields.account_id}.r2.cloudflarestorage.com` : "");
  return <section className={styles.section}>
    <Link href="/admin/integrations/storage" className={styles.back}><ArrowLeft size={16} />Storage</Link>
    <header className={styles.heading}><div><p className={styles.eyebrow}>STORAGE</p><h1>{config.name}</h1></div><button className={styles.primary} disabled={!data || pending || loading} onClick={() => openForm(null)}><Plus size={17} />Add connection</button></header>
    {notice && <div className={styles.notice} role="status"><Check size={18} /><span>{notice}</span><button className={styles.iconButton} aria-label="Dismiss notice" title="Dismiss notice" onClick={() => setNotice("")}><X size={16} /></button></div>}
    <form className={styles.storageFilters} onSubmit={applyFilters}>
      <label className={styles.field}>Search<input type="search" maxLength={160} value={filters.search} onChange={event => setFilters({ ...filters, search: event.target.value })} placeholder="Name or bucket" /></label>
      <label className={styles.field}>Added from (IST)<input type="date" max={filters.to_date || undefined} value={filters.from_date} onChange={event => setFilters({ ...filters, from_date: event.target.value })} /></label>
      <label className={styles.field}>Added to (IST)<input type="date" min={filters.from_date || undefined} value={filters.to_date} onChange={event => setFilters({ ...filters, to_date: event.target.value })} /></label>
      <button className={styles.secondary} disabled={pending}><Search size={16} />Search</button>
      <button className={styles.iconButton} type="button" title="Clear filters" aria-label="Clear filters" disabled={pending} onClick={() => { setFilters({ search: "", from_date: "", to_date: "" }); setQuery(""); setPage(1); refresh(); }}><X size={17} /></button>
    </form>
    {error && <div className={styles.error} role="alert">{error}<button className={styles.secondary} onClick={refresh}><RefreshCw size={16} />Retry</button></div>}
    {loading ? <div className={styles.loading} role="status"><LoaderCircle size={20} className={styles.spin} />Loading connections...</div> : !error && data && <>
      {data.items.length ? <div className={styles.storageTableWrap}><table className={styles.storageTable}><thead><tr><th>Connection</th><th>Bucket / Region</th><th>Status</th><th>Last test</th><th>Added on</th><th><span className={styles.visuallyHidden}>Actions</span></th></tr></thead><tbody>
        {data.items.map(item => <tr key={item.id}>
          <td data-label="Connection"><strong>{item.name}</strong><span className={styles.storageMeta}>{item.endpoint}</span></td>
          <td data-label="Bucket / Region"><strong>{item.bucket}</strong><span className={styles.storageMeta}>{item.region}</span></td>
          <td data-label="Status"><span className={item.enabled ? styles.activeBadge : styles.badge}>{item.enabled ? "Enabled" : "Disabled"}</span></td>
          <td data-label="Last test"><span className={item.last_test_status === "passed" ? styles.activeBadge : styles.badge}>{item.last_test_status === "passed" ? "Passed" : item.last_test_status === "cleanup_failed" ? "Cleanup required" : item.last_test_status === "failed" ? "Failed" : "Not tested"}</span>{item.last_test_status && item.last_test_at && <span className={styles.storageMeta}>{dateText(item.last_test_at)}</span>}</td>
          <td data-label="Added on">{dateText(item.created_at)}</td>
          <td data-label="Actions"><div className={styles.storageRowActions}>
            <button className={styles.iconButton} disabled={pending} title={`Edit ${item.name}`} aria-label={`Edit ${item.name}`} onClick={() => openForm(item)}><Pencil size={16} /></button>
            <button className={styles.iconButton} disabled={pending} title={`Test ${item.name}`} aria-label={`Test ${item.name}`} onClick={() => confirm("test", item)}><ShieldCheck size={17} /></button>
            <button className={styles.iconButton} disabled={pending} title={`Remove ${item.name}`} aria-label={`Remove ${item.name}`} onClick={() => confirm("remove", item)}><Trash2 size={16} /></button>
          </div></td>
        </tr>)}
      </tbody></table></div> : <div className={styles.storageEmpty}><config.icon size={30} /><h2>{hasFilters ? "No matching connections" : "No connections yet"}</h2><button className={styles.secondary} onClick={() => hasFilters ? (setFilters({ search: "", from_date: "", to_date: "" }), setQuery(""), setPage(1), refresh()) : openForm(null)}>{hasFilters ? <X size={16} /> : <Plus size={16} />}{hasFilters ? "Clear filters" : "Add connection"}</button></div>}
      <footer className={styles.storagePagination}><span>{data.total} connection{data.total === 1 ? "" : "s"}</span><div><button className={styles.iconButton} disabled={page <= 1 || pending} title="Previous page" aria-label="Previous page" onClick={() => { setLoading(true); setPage(value => value - 1); }}><ChevronLeft size={18} /></button><span>Page {page} of {Math.max(1, Math.ceil(data.total / 10))}</span><button className={styles.iconButton} disabled={page * 10 >= data.total || pending} title="Next page" aria-label="Next page" onClick={() => { setLoading(true); setPage(value => value + 1); }}><ChevronRight size={18} /></button></div></footer>
    </>}
    <dialog ref={formDialog} className={`${styles.dialog} ${styles.storageDialog}`} aria-labelledby="storage-form-title" onCancel={event => { if (pending) event.preventDefault(); }} onClose={() => { setFields(empty(provider)); setVisible(false); }}>
      <h2 id="storage-form-title">{editing ? "Edit connection" : "Add connection"}</h2>
      {editing?.last_cleanup && <details className={styles.storageCleanup}><summary>Last cleanup warning</summary><p>{dateText(editing.last_cleanup.reported_at)}</p><dl><dt>Bucket</dt><dd>{editing.last_cleanup.bucket}</dd><dt>Endpoint</dt><dd>{editing.last_cleanup.endpoint}</dd><dt>Object key</dt><dd>{editing.last_cleanup.key}</dd></dl></details>}
      <form onSubmit={save}>
        {formError && <div className={styles.error} role="alert">{formError}</div>}
        <fieldset className={styles.formSection} disabled={pending}>
          <div className={styles.fieldGrid}>
            <label className={styles.field}>Connection name<input autoFocus required maxLength={100} value={fields.name} onChange={event => setFields({ ...fields, name: event.target.value })} /></label>
            <label className={styles.field}>Bucket name<input required minLength={3} maxLength={63} value={fields.bucket} autoCapitalize="none" spellCheck={false} onChange={event => setFields({ ...fields, bucket: event.target.value })} /></label>
            {provider === "r2" ? <>
              <label className={styles.field}>Cloudflare account ID<input required pattern="[a-f0-9]{32}" minLength={32} maxLength={32} value={fields.account_id} autoCapitalize="none" spellCheck={false} onChange={event => setFields({ ...fields, account_id: event.target.value, endpoint: "" })} /></label>
              <label className={styles.field}>Jurisdiction<select value={fields.endpoint.includes(".eu.r2.") ? "eu" : "default"} onChange={event => setFields({ ...fields, endpoint: fields.account_id ? `https://${fields.account_id}${event.target.value === "eu" ? ".eu" : ""}.r2.cloudflarestorage.com` : "" })} disabled={!fields.account_id}><option value="default">Default</option><option value="eu">European Union</option></select></label>
            </> : <label className={styles.field}>AWS region<select required value={fields.region} onChange={event => setFields({ ...fields, region: event.target.value })}><option value="">Select region</option>{data?.regions.map(region => <option key={region} value={region}>{region}</option>)}</select></label>}
          </div>
          <p className={styles.endpoint}>S3 endpoint: {endpoint || "Not selected"}{provider === "r2" && " | Region: auto"}</p>
          <div className={styles.fieldGrid}>
            <label className={styles.field}>Access key ID{editing ? " (replace both keys)" : ""}<input required={!editing || Boolean(fields.secret_key)} type={visible ? "text" : "password"} maxLength={256} autoComplete="new-password" spellCheck={false} value={fields.access_key} placeholder={editing ? "Saved securely" : ""} onChange={event => setFields({ ...fields, access_key: event.target.value })} /></label>
            <label className={styles.field}>Secret access key<input required={!editing || Boolean(fields.access_key)} type={visible ? "text" : "password"} maxLength={256} autoComplete="new-password" spellCheck={false} value={fields.secret_key} placeholder={editing ? "Saved securely" : ""} onChange={event => setFields({ ...fields, secret_key: event.target.value })} /></label>
          </div>
          <div className={styles.storageFormTools}><a href={config.docs} target="_blank" rel="noopener noreferrer">Provider credentials &amp; permissions<ArrowUpRight size={15} /></a><button className={styles.iconButton} type="button" title={visible ? "Hide credentials" : "Show credentials"} aria-label={visible ? "Hide credentials" : "Show credentials"} onClick={() => setVisible(value => !value)}>{visible ? <EyeOff size={18} /> : <Eye size={18} />}</button></div>
          <label className={styles.toggle}><input type="checkbox" checked={fields.enabled} onChange={event => setFields({ ...fields, enabled: event.target.checked })} />Enabled</label>
        </fieldset>
        <div className={styles.dialogActions}><button type="button" className={styles.secondary} disabled={pending} onClick={closeForm}>Cancel</button><button className={styles.primary} disabled={pending}>{pending ? <LoaderCircle size={16} className={styles.spin} /> : <Save size={16} />}Save connection</button></div>
      </form>
    </dialog>
    <dialog ref={confirmDialog} className={styles.dialog} aria-labelledby="storage-confirm-title" onCancel={event => { if (pending) event.preventDefault(); }}>
      <h2 id="storage-confirm-title">{action?.type === "test" ? "Test connection?" : "Remove connection?"}</h2>
      <p>{action?.type === "test" ? <>This will create, read and delete a small temporary object in <strong>{action.item.bucket}</strong>. Provider request charges may apply. Retention policies can prevent cleanup.</> : <>Remove saved credentials for <strong>{action?.item.name}</strong>? The bucket and its files will remain unchanged.</>}</p>
      {formError && <div className={styles.error} role="alert">{formError}</div>}
      <div className={styles.dialogActions}><button className={styles.secondary} disabled={pending} onClick={() => { confirmDialog.current?.close(); setAction(null); }}>Cancel</button><button className={action?.type === "test" ? styles.primary : styles.danger} disabled={pending} onClick={runAction}>{pending ? <LoaderCircle size={16} className={styles.spin} /> : action?.type === "test" ? <ShieldCheck size={17} /> : <Trash2 size={16} />}{action?.type === "test" ? "Run test" : "Remove"}</button></div>
    </dialog>
  </section>;
}