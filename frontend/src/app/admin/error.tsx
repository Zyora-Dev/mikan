"use client";

import Link from "next/link";
import styles from "./admin.module.css";

export default function AdminError({ reset }: { reset: () => void }) {
  return <main className={styles.page}><section className={styles.content}><h1>Admin service unavailable</h1><p>Please try again shortly.</p><div className={styles.logout}><button onClick={reset}>Try again</button> <Link href="/admin/login">Back to sign in</Link></div></section></main>;
}