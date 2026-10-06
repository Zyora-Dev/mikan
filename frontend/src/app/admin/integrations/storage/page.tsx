import Link from "next/link";
import { redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { getAdmin } from "@/lib/admin";
import Workspace from "../../workspace";
import styles from "../integrations.module.css";
import { StorageCards } from "./storage-settings";

export const metadata = { title: "Storage Integrations | Mikan" };

export default async function StorageIntegrationsPage() {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");

  return <Workspace admin={admin} active="integrations">
    <section className={styles.section}>
      <Link href="/admin/integrations" className={styles.back}><ArrowLeft size={16} />Integrations</Link>
      <header className={styles.heading}><div><p className={styles.eyebrow}>INTEGRATIONS</p><h1>Storage</h1></div></header>
      <div className={styles.cardGrid}>
        <StorageCards />
      </div>
    </section>
  </Workspace>;
}