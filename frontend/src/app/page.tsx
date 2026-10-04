"use client";

import Image from "next/image";
import { ArrowRight, LockKeyhole } from "lucide-react";
import { useState, type FormEvent } from "react";
import architecture from "../../public/architecture.jpg";
import logo from "../../public/mikan-logo.jpg";
import styles from "./page.module.css";

export default function Home() {
  const [notice, setNotice] = useState("");

  function handleSignIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice("Email sign-in is not connected yet. Please contact your administrator.");
  }

  return (
    <main className={styles.page}>
      <section className={styles.visual} aria-label="Mikan team workspace">
        <Image
          src={architecture}
          alt="Modern glass buildings reaching toward an open sky"
          fill
          priority
          sizes="(max-width: 760px) 100vw, 50vw"
          className={styles.visualImage}
        />
        <div className={styles.visualShade} />
        <div className={styles.visualLabel}>
          <span>MIKAN ENGINEERING</span>
          <span className={styles.rule} />
        </div>
        <div className={styles.visualCopy}>
          <p className={styles.visualTitle}>Your files.<br />Your team.<br /><em>One workspace.</em></p>
          <p className={styles.visualSubtitle}>A place for the work we build together.</p>
        </div>
        <div className={styles.visualFooter}>
          <span>Connected by work. United by Mikan.</span>
          <span className={styles.visualMark} aria-hidden="true">M.</span>
        </div>
      </section>

      <section className={styles.login} aria-labelledby="login-heading">
        <header className={styles.brand}>
          <Image src={logo} alt="Mikan Engineering" className={styles.logo} priority sizes="160px" />
          <span className={styles.brandDivider} />
          <span className={styles.brandLabel}>Cloud Workspace</span>
        </header>

        <div className={styles.formArea}>
          <div className={styles.intro}>
            <p className={styles.eyebrow}>YOUR MIKAN WORKSPACE</p>
            <h1 id="login-heading">Welcome back.</h1>
            <p>Sign in to continue to your team&apos;s workspace.</p>
          </div>
          <form onSubmit={handleSignIn} className={styles.form}>
            <div className={styles.field}>
              <label htmlFor="email">Work email</label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                inputMode="email"
                placeholder="you@company.com"
                required
                maxLength={254}
                onChange={() => setNotice("")}
                aria-describedby={notice ? "signin-notice" : undefined}
              />
            </div>
            <button className={styles.submit} type="submit">
              Continue with email
              <ArrowRight size={18} strokeWidth={1.7} aria-hidden="true" />
            </button>
            <div className={styles.notice}>
              {notice && <p id="signin-notice" role="status">{notice}</p>}
            </div>
          </form>
          <div className={styles.accessNote}>
            <LockKeyhole size={15} strokeWidth={1.6} aria-hidden="true" />
            <span>For authorized Mikan team members</span>
          </div>
          <p className={styles.help}>Need access? <span>Contact your administrator.</span></p>
        </div>

        <footer className={styles.footer}>
          <span>Powered by <a href="https://zyoralabs.com" target="_blank" rel="noopener noreferrer">Zyora Labs <ArrowRight size={12} aria-hidden="true" /></a></span>
          <span className={styles.copyright}>&copy; {new Date().getFullYear()} Mikan Engineering</span>
        </footer>
      </section>
    </main>
  );
}
