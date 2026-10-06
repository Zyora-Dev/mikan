"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Eye, EyeOff, KeyRound, LoaderCircle, Mail } from "lucide-react";
import { teamRequest } from "@/lib/team-client";
import styles from "./page.module.css";

export default function TeamAuth({ initialMethod, returnTo = "/team/files" }: { initialMethod: "otp" | "password"; returnTo?: string }) {
  const router = useRouter();
  const [method, setMethod] = useState<"otp" | "password" | "recover">(initialMethod);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState("");
  const [cooldown, setCooldown] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!cooldown) return;
    const timer = setTimeout(() => setCooldown(value => Math.max(0, value - 1)), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  function changeMethod(next: typeof method) {
    setMethod(next); setPassword(""); setCode(""); setChallenge(""); setVisible(false); setError(""); setNotice("");
  }
  async function sendCode() {
    const result = await teamRequest<{ challenge: string; detail: string; resend_after: number }>("/api/team/auth/otp/request", { email });
    setChallenge(result.challenge); setCode(""); setCooldown(result.resend_after); setNotice(result.detail);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      if (method === "recover") {
        const result = await teamRequest<{ detail: string }>("/api/team/auth/recover", { email });
        setNotice(result.detail);
      } else if (method === "otp" && !challenge) {
        await sendCode();
      } else {
        await teamRequest(method === "password" ? "/api/team/auth/password" : "/api/team/auth/otp/verify", method === "password" ? { email, password } : { challenge, code });
        setPassword(""); setCode(""); router.replace(/^\/share\/[0-9a-f]{64}$/.test(returnTo) ? returnTo : "/team/files"); router.refresh();
      }
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to sign in."); }
    finally { setBusy(false); }
  }
  async function resend() {
    if (busy || cooldown) return;
    setBusy(true); setError("");
    try { await sendCode(); } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to send a code."); }
    finally { setBusy(false); }
  }

  return <>
    {method === "recover" ? <h2 className={styles.smallHeading}>Recover account</h2> : <div className={styles.methodSwitch} aria-label="Sign-in method"><button type="button" disabled={busy} aria-pressed={method === "otp"} onClick={() => changeMethod("otp")}><Mail size={17} />Email OTP</button><button type="button" disabled={busy} aria-pressed={method === "password"} onClick={() => changeMethod("password")}><KeyRound size={17} />Password</button></div>}
    <form onSubmit={submit} className={`${styles.form} ${styles.accessForm}`}>
      <fieldset disabled={busy} className={styles.authFields}>
        <div className={styles.field}><label htmlFor="team-email">Work email</label><input id="team-email" name="email" type="email" autoComplete="username" inputMode="email" placeholder="you@company.com" required maxLength={254} value={email} readOnly={Boolean(challenge)} onChange={event => { setEmail(event.target.value); setError(""); setNotice(""); }} /></div>
        {method === "password" && <div className={styles.field}><label htmlFor="team-password">Password</label><div className={styles.passwordField}><input id="team-password" name="password" type={visible ? "text" : "password"} autoComplete="current-password" required maxLength={128} value={password} onChange={event => setPassword(event.target.value)} /><button type="button" onClick={() => setVisible(!visible)} title={visible ? "Hide password" : "Show password"} aria-label={visible ? "Hide password" : "Show password"}>{visible ? <EyeOff size={18} /> : <Eye size={18} />}</button></div></div>}
        {method === "otp" && challenge && <div className={styles.field}><label htmlFor="team-code">Verification code</label><input id="team-code" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" minLength={6} maxLength={6} required autoFocus value={code} onChange={event => setCode(event.target.value.replace(/\D/g, ""))} placeholder="6-digit code" /><div className={styles.authActions}><button type="button" className={styles.textButton} disabled={busy || cooldown > 0} onClick={() => void resend()}>{cooldown ? `Resend in ${cooldown}s` : "Resend code"}</button><button type="button" className={styles.textButton} onClick={() => { setChallenge(""); setCode(""); setNotice(""); setError(""); }}>Change email</button></div></div>}
        <button className={styles.submit} type="submit" disabled={busy || (method === "otp" && !challenge && cooldown > 0)}>{busy ? <LoaderCircle size={18} className={styles.spin} /> : <ArrowRight size={18} />}{busy ? "Please wait..." : method === "recover" ? "Send recovery link" : method === "password" ? "Sign in" : challenge ? "Verify & sign in" : cooldown ? `Send code in ${cooldown}s` : "Send verification code"}</button>
      </fieldset>
      <div className={styles.authMessage} aria-live="polite">{error && <p className={styles.authError} role="alert">{error}</p>}{notice && <p className={styles.authSuccess} role="status">{notice}</p>}</div>
    </form>
    <div className={styles.authActions}><button type="button" disabled={busy} className={styles.textButton} onClick={() => changeMethod(method === "recover" ? initialMethod : "recover")}>{method === "recover" ? "Back to sign in" : "Forgot password or sign-in method?"}</button></div>
  </>;
}