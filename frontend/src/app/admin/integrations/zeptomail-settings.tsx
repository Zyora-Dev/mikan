"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, ArrowUpRight, Check, Eye, EyeOff, KeyRound, LoaderCircle, Mail, RefreshCw, Save, Send, Settings2, ShieldCheck, Trash2, X } from "lucide-react";
import styles from "./integrations.module.css";

type Settings = {
  endpoint: string; sender_email: string; sender_name: string; enabled: boolean;
  updated_at: string; last_test_at: string | null; last_test_status: "accepted" | "failed" | null;
};
type Configuration = { configured: boolean; settings: Settings | null; endpoints: { url: string; label: string }[] };
type Fields = { endpoint: string; sender_email: string; sender_name: string; enabled: boolean };
const empty: Fields = { endpoint: "", sender_email: "", sender_name: "Mikan", enabled: false };
const endpoint = "/api/admin/integrations/zeptomail";

async function request(router: ReturnType<typeof useRouter>, path = "", options?: RequestInit) {
  const response = await fetch(endpoint + path, { ...options, cache: "no-store", headers: { "Content-Type": "application/json" }, signal: options?.signal ?? AbortSignal.timeout(20000) });
  if (response.status === 401) { router.replace("/admin/login"); router.refresh(); throw new Error("Sign in required."); }
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Unable to complete this request.");
  return body;
}

function displayDate(value: string) {
  return new Date(value).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }) + " IST";
}

export default function ZeptoMailSettings({ view }: { view: "card" | "details" }) {
  const router = useRouter();
  const [configuration, setConfiguration] = useState<Configuration | null>(null);
  const [fields, setFields] = useState<Fields>(empty);
  const [token, setToken] = useState("");
  const [visible, setVisible] = useState(false);
  const [recipient, setRecipient] = useState("");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<"save" | "test" | "remove" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);

  function applyConfiguration(value: Configuration) {
    setConfiguration(value);
    setFields(value.settings ? { endpoint: value.settings.endpoint, sender_email: value.settings.sender_email, sender_name: value.settings.sender_name, enabled: value.settings.enabled } : empty);
    setToken("");
    setVisible(false);
  }

  useEffect(() => {
    const controller = new AbortController();
    request(router, "", { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { applyConfiguration(value); setError(""); }
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load integration.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision, router]);

  const saved = configuration?.settings;
  const baseline = saved ?? empty;
  const dirty = token.length > 0 || Object.keys(empty).some(key => fields[key as keyof Fields] !== baseline[key as keyof Fields]);
  const state = configuration?.configured ? saved?.enabled ? "Enabled" : "Disabled" : "Not configured";

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending("save"); setError(""); setNotice("");
    try {
      applyConfiguration(await request(router, "", { method: "PUT", body: JSON.stringify({ ...fields, token: token.trim() || null }) }));
      setNotice("Credentials saved.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save credentials."); }
    finally { setPending(null); }
  }

  async function sendTest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending("test"); setError(""); setNotice("");
    try {
      const result = await request(router, "/test", { method: "POST", body: JSON.stringify({ recipient }) });
      setNotice(result.detail);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to send test email."); }
    finally {
      const refreshed = await request(router).catch(() => null);
      if (refreshed) setConfiguration(refreshed);
      setPending(null);
    }
  }

  async function remove() {
    setPending("remove"); setError(""); setNotice("");
    try {
      applyConfiguration(await request(router, "", { method: "DELETE", body: "{}" }));
      setNotice("Saved credentials removed.");
      dialog.current?.close();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to remove credentials."); dialog.current?.close(); }
    finally { setPending(null); }
  }

  const Container = view === "card" ? "div" : "section";

  return <Container className={view === "card" ? styles.cardSlot : styles.section}>
    {view === "details" && <Link href="/admin/integrations" className={styles.back}><ArrowLeft size={16} />Integrations</Link>}
    {view === "details" && <header className={styles.heading}><div><p className={styles.eyebrow}>ZOHO CPAAS</p><h1>ZeptoMail</h1></div><a className={styles.secondary} href="https://www.zoho.com/cpaas/help/api/api-authentication.html" target="_blank" rel="noopener noreferrer">API credentials <ArrowUpRight size={15} /></a></header>}
    {error && <div role="alert" className={styles.error}>{error}{!configuration && !loading && <button className={styles.secondary} onClick={() => { setLoading(true); setRevision(value => value + 1); }}><RefreshCw size={15} />Retry</button>}</div>}
    {notice && <div role="status" className={styles.notice}><Check size={17} /><span>{notice}</span><button type="button" className={styles.iconButton} onClick={() => setNotice("")} title="Dismiss notification" aria-label="Dismiss notification"><X size={16} /></button></div>}
    {loading ? <div className={styles.loading} role="status"><LoaderCircle size={20} className={styles.spin} />Loading integration...</div> : configuration && (view === "card" ?
      <article className={styles.card}>
        <div className={styles.cardTop}><span className={styles.providerIcon}><Mail size={27} /></span><span className={saved?.enabled ? styles.activeBadge : styles.badge}>{state}</span></div>
        <h2>ZeptoMail</h2><p className={styles.provider}>Zoho CPaaS</p>
        <dl className={styles.summary}><div><dt>Channel</dt><dd>Transactional email</dd></div><div><dt>Scope</dt><dd>All companies</dd></div><div><dt>Sender</dt><dd>{saved?.sender_email || "Not configured"}</dd></div></dl>
        <Link href="/admin/integrations/zeptomail" className={styles.cardAction}><Settings2 size={16} />{configuration.configured ? "Manage credentials" : "Configure"}<ArrowRight size={17} /></Link>
      </article> :
      <div className={styles.details}>
        <div className={styles.statusRow}><span className={saved?.enabled ? styles.activeBadge : styles.badge}>{state}</span><span><ShieldCheck size={16} />Platform-wide</span>{saved && <span>Updated {displayDate(saved.updated_at)}</span>}</div>
        <form onSubmit={save}>
          <fieldset className={styles.formSection} disabled={pending !== null}>
            <legend><KeyRound size={18} />Connection</legend>
            <label className={styles.field}>Data center<select required value={fields.endpoint} onChange={event => setFields({ ...fields, endpoint: event.target.value })}><option value="">Select data center</option>{configuration.endpoints.map(item => <option key={item.url} value={item.url}>{item.label}</option>)}</select></label>
            {fields.endpoint && <p className={styles.endpoint}>{fields.endpoint}</p>}
            <label className={styles.field} htmlFor="zepto-token">{configuration.configured ? "Replace API token (optional)" : "API token"}</label>
            <div className={styles.secretInput}><input id="zepto-token" type={visible ? "text" : "password"} required={!configuration.configured} maxLength={4096} autoComplete="new-password" spellCheck={false} value={token} onChange={event => setToken(event.target.value)} placeholder={configuration.configured ? "Saved token unchanged" : "Agent API key"} /><button type="button" className={styles.iconButton} onClick={() => setVisible(!visible)} title={visible ? "Hide entered token" : "Show entered token"} aria-label={visible ? "Hide entered token" : "Show entered token"}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div>
            {configuration.configured && <p className={styles.savedToken}><ShieldCheck size={15} />API token saved and encrypted</p>}
          </fieldset>
          <fieldset className={styles.formSection} disabled={pending !== null}>
            <legend><Mail size={18} />Sender</legend>
            <div className={styles.fieldGrid}><label className={styles.field}>Sender name<input required maxLength={100} value={fields.sender_name} onChange={event => setFields({ ...fields, sender_name: event.target.value })} autoComplete="organization" /></label><label className={styles.field}>Verified sender email<input required type="email" maxLength={254} value={fields.sender_email} onChange={event => setFields({ ...fields, sender_email: event.target.value })} autoComplete="email" placeholder="notifications@company.com" /></label></div>
            <label className={styles.toggle}><input type="checkbox" checked={fields.enabled} onChange={event => setFields({ ...fields, enabled: event.target.checked })} />Enable transactional email</label>
          </fieldset>
          <div className={styles.actions}>{dirty && <span>Unsaved changes</span>}<button type="button" className={styles.secondary} disabled={!!pending || !dirty} onClick={() => applyConfiguration(configuration)}>Reset</button><button className={styles.primary} disabled={!!pending || (configuration.configured && !dirty)}>{pending === "save" ? <LoaderCircle size={16} className={styles.spin} /> : <Save size={16} />}Save credentials</button></div>
        </form>
        <form className={styles.testSection} onSubmit={sendTest}><h2><Send size={18} />Test email</h2><div className={styles.testRow}><label className={styles.field}>Recipient email<input type="email" required maxLength={254} value={recipient} onChange={event => setRecipient(event.target.value)} disabled={!!pending || !configuration.configured} placeholder="you@company.com" /></label><button className={styles.secondary} disabled={!!pending || !configuration.configured || dirty} title={dirty ? "Save changes before testing" : "Send a real test email"}>{pending === "test" ? <LoaderCircle size={16} className={styles.spin} /> : <Send size={16} />}Send test email</button></div><p className={styles.testStatus}>{saved?.last_test_at ? `Last test: ${saved.last_test_status === "accepted" ? "Accepted by Zoho" : "Failed"} - ${displayDate(saved.last_test_at)}` : "No test email sent"}</p></form>
        {configuration.configured && <div className={styles.removeSection}><h2>Remove integration</h2><button type="button" className={styles.danger} disabled={!!pending} onClick={() => dialog.current?.showModal()}><Trash2 size={16} />Remove credentials</button></div>}
      </div>)}
    <dialog ref={dialog} className={styles.dialog} aria-labelledby="remove-zeptomail-title" onCancel={event => { if (pending) event.preventDefault(); }}><h2 id="remove-zeptomail-title">Remove ZeptoMail credentials?</h2><p>This removes the saved API token and sender settings from Mikan. Your Zoho account is unchanged.</p><div className={styles.dialogActions}><button type="button" className={styles.secondary} disabled={!!pending} onClick={() => dialog.current?.close()}>Cancel</button><button type="button" className={styles.danger} disabled={!!pending} onClick={() => void remove()}>{pending === "remove" ? <LoaderCircle size={16} className={styles.spin} /> : <Trash2 size={16} />}Remove credentials</button></div></dialog>
  </Container>;
}