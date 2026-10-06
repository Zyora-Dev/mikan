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
    const fields = new FormData(event.currentTarget);
    setPending(true);
    setError("");
    try {
      const response = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: String(fields.get("email")).trim(), password: fields.get("password") }),
      });
      const result = await response.json();
      if (!response.ok) {
        setError(typeof result.detail === "string" ? result.detail : "Unable to sign in. Check your details.");
        setPending(false);
        return;
      }
      router.replace("/admin");
      router.refresh();
    } catch {
      setError("Unable to reach the admin service. Please try again.");
      setPending(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.visual} aria-label="Mikan Engineering">
        <Image src="/architecture.jpg" alt="Architectural lines of modern office towers" fill priority sizes="(max-width: 760px) 100vw, 55vw" className={styles.photo} />
        <div className={styles.visualTop}><span>MIKAN ENGINEERING</span><span>ADMINISTRATION</span></div>
        <div className={styles.visualBottom}><span className={styles.line} /><p>Built on trust.<br />Managed with care.</p><span>MIKAN CLOUD WORKSPACE</span></div>
      </section>
      <section className={styles.panel} aria-labelledby="admin-heading">
        <div className={styles.topbar}>
          <Link href="/" className={styles.back}><ArrowLeft size={15} /> Team sign in</Link>
          <span className={styles.secure}><ShieldCheck size={15} /> Admin access</span>
        </div>
        <div className={styles.formArea}>
          <Image src="/mikan-logo.jpg" width={185} height={73} alt="Mikan Engineering" priority className={styles.logo} />
          <div className={styles.heading}><p>SUPER ADMIN</p><h1 id="admin-heading">Sign in.</h1><span>Welcome back to your admin workspace.</span></div>
          <form onSubmit={signIn} onChange={() => error && setError("")} className={styles.form}>
            <div className={styles.field}>
              <label htmlFor="admin-email">Email address</label>
              <input id="admin-email" name="email" type="email" autoComplete="username" placeholder="admin@company.com" required maxLength={254} disabled={pending} aria-describedby={error ? "admin-error" : undefined} />
            </div>
            <div className={styles.field}>
              <label htmlFor="admin-password">Password</label>
              <div className={styles.password}>
                <input id="admin-password" name="password" type={visible ? "text" : "password"} autoComplete="current-password" placeholder="Enter your password" required maxLength={128} disabled={pending} aria-describedby={error ? "admin-error" : undefined} />
                <button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? "Hide password" : "Show password"} title={visible ? "Hide password" : "Show password"} aria-pressed={visible} disabled={pending}>{visible ? <EyeOff size={19} /> : <Eye size={19} />}</button>
              </div>
            </div>
            <div className={styles.errorSpace}>{error && <p id="admin-error" role="alert">{error}</p>}</div>
            <button type="submit" className={styles.submit} disabled={pending}>{pending ? <>Signing in <LoaderCircle size={18} className={styles.spinner} /></> : <>Sign in <ArrowRight size={18} /></>}</button>
          </form>
          <p className={styles.access}><ShieldCheck size={16} /> Authorized super administrators only</p>
        </div>
        <footer className={styles.footer}><span>Mikan Engineering</span><span>Powered by <a href="https://zyoralabs.com" target="_blank" rel="noopener noreferrer">Zyora Labs <ArrowRight size={12} /></a></span></footer>
      </section>
    </main>
  );
}