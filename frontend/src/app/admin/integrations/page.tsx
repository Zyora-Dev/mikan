import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import Workspace from "../workspace";
import ZeptoMailSettings from "./zeptomail-settings";
import { StorageCards } from "./storage/storage-settings";
import styles from "./integrations.module.css";

export const metadata = { title: "Integrations | Mikan" };

export default async function IntegrationsPage() {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  return <Workspace admin={admin} active="integrations">
    <section className={styles.section}>
      <header className={styles.heading}><div><p className={styles.eyebrow}>WORKSPACE</p><h1>Integrations</h1></div></header>
      <div className={styles.cardGrid}>
        <ZeptoMailSettings view="card" />
        <StorageCards overview />
      </div>
    </section>
  </Workspace>;
}