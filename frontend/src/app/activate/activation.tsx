"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { CheckCircle2, Eye, EyeOff, KeyRound, LoaderCircle, Mail } from "lucide-react";
import { teamRequest } from "@/lib/team-client";
import styles from "../page.module.css";

type Invitation = { name: string; email: string; role: "manager" | "member"; team_name: string; company_name: string; purpose: "activate" | "recover" };

export default function Activation() {
  const token = useRef<string | null>(null);
  const [invitation, setInvitation] = useState<Invitation | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [authType, setAuthType] = useState<"otp" | "password">("otp");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    if (token.current === null) {
      token.current = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
      window.history.replaceState(null, "", "/activate");
    }
    const value = token.current;
    async function load() {
      setLoading(true); setError("");
      if (!value) { setError("Open the verification link from your email. Ask your administrator to resend an expired invitation."); setLoading(false); return; }
      try {
        const data = await teamRequest<Invitation>("/api/team/auth/verification", { token: value }, controller.signal);
        if (!controller.signal.aborted) setInvitation(data);
      } catch (failure) { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to verify this link."); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [revision]);

  async function activate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError("");
    if (authType === "password" && password !== confirmation) { setError("Passwords do not match."); return; }
    setBusy(true);
    try {
      await teamRequest("/api/team/auth/activate", { token: token.current, auth_type: authType, ...(authType === "password" ? { password } : {}) });
      setDone(true); token.current = ""; setPassword(""); setConfirmation("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to activate your account."); }
    finally { setBusy(false); }
  }

  if (loading) return <div className={styles.authState} role="status"><LoaderCircle size={24} className={styles.spin} />Verifying your link...</div>;
  if (done) return <div className={styles.authState}><CheckCircle2 size={32} /><h2 className={styles.smallHeading}>Account verified</h2><p>{authType === "otp" ? "Email OTP" : "Password"} sign-in is enabled.</p><Link className={styles.submit} href={`/?method=${authType}`}>Continue to sign in</Link></div>;
  if (!invitation) return <div className={styles.authState}><p className={styles.authError} role="alert">{error}</p><button className={styles.textButton} onClick={() => setRevision(value => value + 1)}>Try again</button><Link className={styles.textButton} href="/">Back to sign in</Link></div>;

  return <><div className={styles.invitation}><h2 className={styles.smallHeading}>{invitation.purpose === "recover" ? "Recover your account" : "Activate your account"}</h2><strong>{invitation.name}</strong><span>{invitation.email}</span><span>{invitation.company_name} / {invitation.team_name}</span><span>{invitation.role === "manager" ? "Team Manager" : "Member"}</span></div><form onSubmit={activate} className={styles.accessForm}><fieldset className={styles.authFields} disabled={busy}><legend className={styles.authLegend}>Choose your sign-in method</legend><div className={styles.methodSwitch} aria-label="Preferred sign-in method"><button type="button" aria-pressed={authType === "otp"} onClick={() => { setAuthType("otp"); setPassword(""); setConfirmation(""); setVisible(false); }}><Mail size={17} />Email OTP</button><button type="button" aria-pressed={authType === "password"} onClick={() => setAuthType("password")}><KeyRound size={17} />Password</button></div>{authType === "password" && <><div className={styles.field}><label htmlFor="new-password">Password (12-128 characters)</label><div className={styles.passwordField}><input id="new-password" name="password" type={visible ? "text" : "password"} minLength={12} maxLength={128} required autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} /><button type="button" onClick={() => setVisible(!visible)} title={visible ? "Hide password" : "Show password"} aria-label={visible ? "Hide password" : "Show password"}>{visible ? <EyeOff size={18} /> : <Eye size={18} />}</button></div></div><div className={styles.field}><label htmlFor="confirm-password">Confirm password</label><input id="confirm-password" name="confirm-password" type={visible ? "text" : "password"} minLength={12} maxLength={128} required autoComplete="new-password" value={confirmation} onChange={event => setConfirmation(event.target.value)} /></div></>}<button type="submit" className={styles.submit} disabled={busy}>{busy && <LoaderCircle size={18} className={styles.spin} />}{busy ? "Verifying..." : invitation.purpose === "recover" ? "Save sign-in method" : "Activate account"}</button></fieldset>{error && <p className={styles.authError} role="alert">{error}</p>}</form><p className={styles.help}><Link href="/">Back to sign in</Link></p></>;
}