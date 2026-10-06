import Image from "next/image";
import Activation from "./activation";
import styles from "../page.module.css";

export const metadata = { title: "Verify Account | Mikan", robots: { index: false, follow: false }, referrer: "no-referrer" as const };

export default function ActivationPage() {
  return <main className={styles.activationPage}><header className={styles.brand}><Image src="/mikan-logo.jpg" alt="Mikan Engineering" width={160} height={65} className={styles.logo} priority /><span className={styles.brandDivider} /><span className={styles.brandLabel}>Cloud Workspace</span></header><section className={styles.activationContent} aria-labelledby="activation-heading"><h1 id="activation-heading">Mikan Cloud</h1><Activation /></section></main>;
}