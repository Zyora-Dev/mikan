"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Eye, EyeOff, LoaderCircle, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import styles from "./login.module.css";

export default function LoginForm() {
  const router = useRouter();
  const [visible, setVisible] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    setPending(true); setError("");
    try {
      const response = await fetch("/api/company/admin/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: String(fields.get("email")).trim(), password: fields.get("password") }),
        signal: AbortSignal.timeout(15000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Unable to sign in. Check your details.");
      form.reset(); setVisible(false);
      router.replace("/company/admin"); router.refresh();
    } catch (failure) {
      setError(failure instanceof Error && failure.name !== "TimeoutError" ? failure.message : "Unable to reach the company admin service. Please try again.");
      setPending(false);
    }
  }

  return <main className={styles.page}>
    <section className={styles.visual} aria-label="Mikan Engineering">
      <Image src="/architecture.jpg" alt="Modern engineering and architectural structures" fill priority sizes="(max-width: 760px) 100vw, 48vw" className={styles.photo} />
      <div className={styles.tint} />
      <div className={styles.visualHeader}><Image src="/mikan-logo.jpg" alt="Mikan Engineering" width={144} height={57} priority className={styles.logo} /><span>COMPANY WORKSPACE</span></div>
      <div className={styles.visualFooter}><span className={styles.rule} /><p>Built together.<br />Moving forward.</p><span>MIKAN ENGINEERING</span></div>
    </section>
    <section className={styles.panel} aria-labelledby="company-login-title">
      <header className={styles.topbar}><Link href="/"><ArrowLeft size={15} />Team sign in</Link><span><ShieldCheck size={16} />Admin access</span></header>
      <div className={styles.formArea}>
        <p className={styles.eyebrow}>COMPANY ADMIN</p>
        <h1 id="company-login-title">Login to your account</h1>
        <p className={styles.subtitle}>Welcome back to your company workspace.</p>
        <form onSubmit={signIn} onChange={() => error && setError("")} className={styles.form} aria-busy={pending}>
          <div className={styles.field}><label htmlFor="company-email">Email <span>*</span></label><input id="company-email" name="email" type="email" autoComplete="username" placeholder="Enter email address" maxLength={254} required disabled={pending} aria-describedby={error ? "company-login-error" : undefined} /></div>
          <div className={styles.field}><label htmlFor="company-password">Password <span>*</span></label><div className={styles.password}><input id="company-password" name="password" type={visible ? "text" : "password"} autoComplete="current-password" placeholder="Enter password" maxLength={128} required disabled={pending} aria-describedby={error ? "company-login-error" : undefined} /><button type="button" onClick={() => setVisible(!visible)} disabled={pending} aria-label={visible ? "Hide password" : "Show password"} title={visible ? "Hide password" : "Show password"} aria-pressed={visible}>{visible ? <EyeOff size={19} /> : <Eye size={19} />}</button></div></div>
          <div className={styles.errorSpace}>{error && <p role="alert" id="company-login-error">{error}</p>}</div>
          <button className={styles.submit} type="submit" disabled={pending}>{pending ? <>Signing in<LoaderCircle size={18} className={styles.spinner} /></> : <>Login<ArrowRight size={18} /></>}</button>
        </form>
        <p className={styles.access}><ShieldCheck size={15} />Company administrators only</p>
      </div>
      <footer className={styles.footer}><span>Mikan Engineering</span><a href="https://zyoralabs.com" target="_blank" rel="noopener noreferrer">Powered by Zyora Labs<ArrowRight size={13} /></a></footer>
    </section>
  </main>;
}