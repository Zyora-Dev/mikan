"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Ban, ChevronLeft, ChevronRight, Eye, EyeOff, LoaderCircle, Plus, RefreshCw, Save, Send, Settings2, ShieldCheck } from "lucide-react";
import styles from "../integrations/integrations.module.css";
import local from "./whatsapp.module.css";

type Tab = "send" | "consents" | "settings";
type Configuration = { configured: boolean; settings: { phone_number_id: string; business_id: string } | null };
type Consent = { id: number; company_name: string; recipient: string; source: string; consented_at: string; revoked_at: string | null; last_attempt_at: string | null; last_status: string | null; last_message_id: string | null };
type ConsentList = { items: Consent[]; total: number };
const endpoint = "/api/admin/integrations/whatsapp";
const initialConsent = { company_name: "", recipient: "", source: "", opted_in: false };
const tabs = [{ id: "send" as const, label: "Send Template", icon: Send }, { id: "consents" as const, label: "Consents", icon: ShieldCheck }, { id: "settings" as const, label: "Settings", icon: Settings2 }];
const displayDate = (value: string) => new Date(value).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }) + " IST";

export default function WhatsAppModule() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("send");
  const [configuration, setConfiguration] = useState<Configuration | null>(null);
  const [settingsRevision, setSettingsRevision] = useState(0);
  const [phoneId, setPhoneId] = useState("");
  const [businessId, setBusinessId] = useState("");
  const [token, setToken] = useState("");
  const [visible, setVisible] = useState(false);
  const [consent, setConsent] = useState(initialConsent);
  const [rows, setRows] = useState<ConsentList>({ items: [], total: 0 });
  const [search, setSearch] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<Consent | null>(null);
  const [companyName, setCompanyName] = useState("");
  const [revoking, setRevoking] = useState<Consent | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const busy = useRef(false);
  const consentDialog = useRef<HTMLDialogElement>(null);
  const sendDialog = useRef<HTMLDialogElement>(null);
  const revokeDialog = useRef<HTMLDialogElement>(null);

  async function request(path: string, options?: RequestInit) {
    try {
      const response = await fetch(endpoint + path, { ...options, cache: "no-store", headers: { "Content-Type": "application/json" }, signal: options?.signal ?? AbortSignal.timeout(20000) });
      if (response.status === 401) { router.replace("/admin/login"); router.refresh(); }
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Unable to complete this request.");
      return body;
    } catch (reason) {
      if (path === "/send") throw new Error(reason instanceof Error && reason.name === "Error" ? reason.message : "Send outcome is unknown. Check WhatsApp before sending again to avoid a duplicate.");
      throw reason;
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    fetch(endpoint, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) }).then(async response => {
      if (response.status === 401) { router.replace("/admin/login"); router.refresh(); }
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Unable to load settings.");
      if (!controller.signal.aborted) { setConfiguration(body); setPhoneId(body.settings?.phone_number_id ?? ""); setBusinessId(body.settings?.business_id ?? ""); }
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load settings."); });
    return () => controller.abort();
  }, [router, settingsRevision]);

  useEffect(() => {
    if (tab === "settings") return;
    const controller = new AbortController();
    const query = new URLSearchParams({ page: String(page), search, from_date: fromDate, to_date: toDate, active: String(tab === "send") });
    if (!fromDate) query.delete("from_date");
    if (!toDate) query.delete("to_date");
    const timer = setTimeout(() => {
      fetch(`${endpoint}/consents?${query}`, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) }).then(async response => {
        if (response.status === 401) { router.replace("/admin/login"); router.refresh(); }
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Unable to load consent records.");
        if (!controller.signal.aborted) setRows(body);
      }).catch(reason => { if (!controller.signal.aborted) { setRows({ items: [], total: 0 }); setError(reason instanceof Error ? reason.message : "Unable to load consents."); } }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [tab, page, search, fromDate, toDate, revision, router]);

  function refresh() { setLoading(true); setSelected(null); setRevision(value => value + 1); }
  function filter(change: () => void) { change(); setPage(1); setSelected(null); setLoading(true); setError(""); }
  async function perform(action: () => Promise<void>) {
    if (busy.current) return;
    busy.current = true; setPending(true); setError(""); setNotice("");
    try { await action(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to complete this request."); }
    finally { busy.current = false; setPending(false); }
  }
  function saveSettings(event: FormEvent) {
    event.preventDefault();
    void perform(async () => {
      const body = await request("", { method: "PUT", body: JSON.stringify({ phone_number_id: phoneId, business_id: businessId, access_token: token.trim() || null }) });
      setConfiguration(body); setPhoneId(body.settings.phone_number_id); setBusinessId(body.settings.business_id); setToken(""); setVisible(false); setNotice("WhatsApp credentials saved.");
    });
  }
  function saveConsent(event: FormEvent) {
    event.preventDefault();
    void perform(async () => {
      await request("/consents", { method: "POST", body: JSON.stringify(consent) });
      consentDialog.current?.close(); setConsent(initialConsent); setNotice("Consent recorded."); setPage(1); refresh();
    });
  }
  function send() {
    if (!selected) return;
    void perform(async () => {
      try {
        const result = await request("/send", { method: "POST", body: JSON.stringify({ consent_id: selected.id, company_name: companyName }) });
        if (result.status === "accepted") setNotice(`${result.detail} Message ID: ${result.message_id}`);
        else setError(result.detail);
      } finally { sendDialog.current?.close(); refresh(); }
    });
  }
  const dirty = token.length > 0 || phoneId !== (configuration?.settings?.phone_number_id ?? "") || businessId !== (configuration?.settings?.business_id ?? "");
  const pagination = <div className={styles.storagePagination}><span>{rows.total} consent{rows.total === 1 ? "" : "s"} - Page {page} of {Math.max(1, Math.ceil(rows.total / 10))}</span><div><button type="button" className={styles.iconButton} title="Previous page" aria-label="Previous page" disabled={pending || loading || page === 1} onClick={() => { setPage(page - 1); refresh(); }}><ChevronLeft size={18} /></button><button type="button" className={styles.iconButton} title="Next page" aria-label="Next page" disabled={pending || loading || page * 10 >= rows.total} onClick={() => { setPage(page + 1); refresh(); }}><ChevronRight size={18} /></button></div></div>;

  return <section className={styles.section}>
    <header className={styles.heading}><div><p className={styles.eyebrow}>BUSINESS MESSAGING</p><h1>WhatsApp</h1></div><span className={configuration?.configured ? styles.activeBadge : styles.badge}>{configuration ? configuration.configured ? "Configured" : "Not configured" : "Loading settings"}</span></header>
    <div className={local.tabs} role="tablist" aria-label="WhatsApp">
      {tabs.map(item => <button id={`tab-${item.id}`} aria-controls={`panel-${item.id}`} role="tab" key={item.id} aria-selected={tab === item.id} disabled={pending} onClick={() => filter(() => { setTab(item.id); setSearch(""); setFromDate(""); setToDate(""); })}><item.icon size={17} />{item.label}</button>)}
    </div>
    {error && <div className={styles.error} role="alert">{error}</div>}
    {!configuration && error && <button className={styles.secondary} disabled={pending} onClick={() => { setError(""); setSettingsRevision(value => value + 1); }}><RefreshCw size={16} />Retry settings</button>}
    {notice && <div className={styles.notice} role="status">{notice}</div>}
    <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
      {tab === "settings" ? <form className={styles.details} onSubmit={saveSettings}>
        <fieldset className={`${styles.formSection} ${local.stack}`} disabled={pending}><legend><Settings2 size={18} />Connection</legend>
          <div className={styles.fieldGrid}><label className={styles.field}>Phone number ID<input required inputMode="numeric" pattern="[1-9][0-9]{4,29}" value={phoneId} onChange={event => setPhoneId(event.target.value)} /></label><label className={styles.field}>Business ID<input required inputMode="numeric" pattern="[1-9][0-9]{4,29}" value={businessId} onChange={event => setBusinessId(event.target.value)} /></label></div>
          <div><label className={styles.field} htmlFor="whatsapp-token">{configuration?.configured ? "Replace access token (optional)" : "Access token"}</label><div className={styles.secretInput}><input id="whatsapp-token" required={!configuration?.configured} type={visible ? "text" : "password"} autoComplete="new-password" spellCheck={false} maxLength={4096} value={token} onChange={event => setToken(event.target.value)} placeholder={configuration?.configured ? "Saved token unchanged" : "Meta access token"} /><button type="button" className={styles.iconButton} onClick={() => setVisible(!visible)} title={visible ? "Hide token" : "Show token"} aria-label={visible ? "Hide token" : "Show token"}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></div>
          {configuration?.configured && <p className={styles.savedToken}><ShieldCheck size={16} />Access token saved and encrypted</p>}
        </fieldset><div className={styles.actions}><button className={styles.primary} disabled={pending || !dirty}>{pending ? <LoaderCircle size={16} className={styles.spin} /> : <Save size={16} />}Save settings</button></div>
      </form> : <>
        <div className={local.tools}><h2>{tab === "send" ? "Onboarding message" : "Consent records"}</h2><div className={styles.storageRowActions}><button type="button" className={styles.secondary} disabled={pending} onClick={() => { setConsent(initialConsent); consentDialog.current?.showModal(); }}><Plus size={16} />Add consent</button><button type="button" className={styles.iconButton} title="Refresh consents" aria-label="Refresh consents" disabled={pending || loading} onClick={refresh}><RefreshCw size={17} /></button></div></div>
        <div className={styles.storageFilters}><label className={styles.field}>Search recipient or company<input value={search} disabled={pending} onChange={event => filter(() => setSearch(event.target.value))} maxLength={160} /></label><label className={styles.field}>Consent from<input type="date" value={fromDate} max={toDate || undefined} disabled={pending} onChange={event => filter(() => setFromDate(event.target.value))} /></label><label className={styles.field}>Consent to<input type="date" value={toDate} min={fromDate || undefined} disabled={pending} onChange={event => filter(() => setToDate(event.target.value))} /></label></div>
        {loading ? <div className={styles.loading} role="status"><LoaderCircle size={19} className={styles.spin} />Loading consents...</div> : tab === "send" ? <form className={styles.details} onSubmit={event => { event.preventDefault(); sendDialog.current?.showModal(); }}>
          <dl className={local.template}><div><dt>Template</dt><dd>onboarding_client</dd></div><div><dt>Language</dt><dd>en</dd></div><div><dt>Body variable</dt><dd>Company name</dd></div></dl>
          <fieldset className={`${styles.formSection} ${local.stack}`} disabled={pending || !configuration?.configured}>
            <label className={styles.field}>Recipient number<select required value={selected?.id ?? ""} onChange={event => { const chosen = rows.items.find(item => item.id === Number(event.target.value)) ?? null; setSelected(chosen); setCompanyName(chosen?.company_name ?? ""); }}><option value="">{rows.total ? "Select consented recipient" : "No active consents found"}</option>{rows.items.map(item => <option key={item.id} value={item.id}>{item.recipient} - {item.company_name}</option>)}</select></label>
            {pagination}
            <label className={styles.field}>Company name<input required maxLength={160} value={companyName} onChange={event => setCompanyName(event.target.value)} /></label>
            {selected?.last_attempt_at && <p className={styles.testStatus}>Last attempt: {displayDate(selected.last_attempt_at)} · {selected.last_status}{selected.last_message_id && <span className={styles.storageMeta}>{selected.last_message_id}</span>}</p>}
          </fieldset>
          {!configuration?.configured && <p className={styles.error}>WhatsApp credentials are not configured.</p>}
          <div className={styles.actions}>{dirty && <span>Unsaved settings changes</span>}<button className={styles.primary} disabled={pending || !selected || !configuration?.configured || dirty}><Send size={16} />Send template</button></div>
        </form> : <>
          {rows.items.length ? <div className={styles.storageTableWrap}><table className={styles.storageTable}><thead><tr><th>Company / number</th><th>Consent evidence</th><th>Status</th><th>Recorded</th><th>Actions</th></tr></thead><tbody>{rows.items.map(item => <tr key={item.id}><td data-label="Company"><strong>{item.company_name}</strong><span className={styles.storageMeta}>{item.recipient}</span></td><td data-label="Evidence">{item.source}</td><td data-label="Status"><span className={item.revoked_at ? styles.badge : styles.activeBadge}>{item.revoked_at ? "Revoked" : "Active"}</span></td><td data-label="Recorded">{displayDate(item.consented_at)}{item.revoked_at && <span className={styles.storageMeta}>Revoked {displayDate(item.revoked_at)}</span>}{item.last_status && <span className={styles.storageMeta}>Last send: {item.last_status}</span>}</td><td data-label="Actions">{!item.revoked_at && <button className={styles.iconButton} title={`Revoke consent for ${item.recipient}`} aria-label={`Revoke consent for ${item.recipient}`} disabled={pending} onClick={() => { setRevoking(item); revokeDialog.current?.showModal(); }}><Ban size={17} /></button>}</td></tr>)}</tbody></table></div> : <div className={styles.storageEmpty}><ShieldCheck size={25} /><h2>No consent records found</h2><button className={styles.secondary} onClick={() => consentDialog.current?.showModal()}><Plus size={16} />Add consent</button></div>}
          {pagination}
        </>}
      </>}
    </div>
    <dialog ref={consentDialog} className={`${styles.dialog} ${styles.storageDialog}`} aria-labelledby="consent-title" onCancel={event => { if (pending) event.preventDefault(); }}><h2 id="consent-title">Record WhatsApp consent</h2><form onSubmit={saveConsent}><fieldset className={`${styles.formSection} ${local.stack}`} disabled={pending}>
      <div className={styles.fieldGrid}><label className={styles.field}>Company name<input required maxLength={160} value={consent.company_name} onChange={event => setConsent({ ...consent, company_name: event.target.value })} /></label><label className={styles.field}>Recipient number<input type="tel" required pattern="\+[1-9][0-9]{7,14}" maxLength={16} placeholder="+919876543210" value={consent.recipient} onChange={event => setConsent({ ...consent, recipient: event.target.value })} /></label></div>
      <label className={styles.field}>Consent source / evidence<input required minLength={3} maxLength={500} placeholder="Signed form reference or opt-in record" value={consent.source} onChange={event => setConsent({ ...consent, source: event.target.value })} /></label>
      <label className={local.consentText}><input type="checkbox" required checked={consent.opted_in} onChange={event => setConsent({ ...consent, opted_in: event.target.checked })} />I confirm this recipient explicitly agreed to receive WhatsApp onboarding messages from our business.</label>
    </fieldset>{error && <p className={styles.error} role="alert">{error}</p>}<div className={styles.dialogActions}><button type="button" className={styles.secondary} disabled={pending} onClick={() => consentDialog.current?.close()}>Cancel</button><button className={styles.primary} disabled={pending}><Save size={16} />Record consent</button></div></form></dialog>
    <dialog ref={sendDialog} className={styles.dialog} aria-labelledby="send-title" onCancel={event => { if (pending) event.preventDefault(); }}><h2 id="send-title">Send onboarding message?</h2><p><strong>{selected?.recipient}</strong><br />Company: <strong>{companyName}</strong><br />Template: onboarding_client · en</p><div className={styles.dialogActions}><button type="button" className={styles.secondary} disabled={pending} onClick={() => sendDialog.current?.close()}>Cancel</button><button type="button" className={styles.primary} disabled={pending} onClick={send}>{pending ? <LoaderCircle size={16} className={styles.spin} /> : <Send size={16} />}Send message</button></div></dialog>
    <dialog ref={revokeDialog} className={styles.dialog} aria-labelledby="revoke-title" onCancel={event => { if (pending) event.preventDefault(); }}><h2 id="revoke-title">Revoke consent?</h2><p><strong>{revoking?.recipient}</strong> will no longer be available for sending. The consent record remains for reference.</p>{error && <p className={styles.error} role="alert">{error}</p>}<div className={styles.dialogActions}><button type="button" className={styles.secondary} disabled={pending} onClick={() => revokeDialog.current?.close()}>Cancel</button><button type="button" className={styles.danger} disabled={pending} onClick={() => void perform(async () => { await request(`/consents/${revoking?.id}/revoke`, { method: "POST", body: "{}" }); revokeDialog.current?.close(); setNotice("Consent revoked."); refresh(); })}><Ban size={16} />Revoke consent</button></div></dialog>
  </section>;
}