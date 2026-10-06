import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import Workspace from "./workspace";
import styles from "./admin.module.css";

export const metadata = { title: "Super Admin | Zyora Labs" };

export default async function AdminPage() {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  return (
    <Workspace admin={admin} active="overview">
        <section className={styles.content}>
          <p className={styles.role}>ZYORA LABS</p>
          <h1>Welcome, {admin.name}.</h1>
          <p className={styles.subtitle}>Your administrator account</p>
          <div className={styles.sectionHeading}><h2>Account details</h2><span className={styles.active}><span />Active</span></div>
          <dl>
            <div><dt>Email address</dt><dd>{admin.email}</dd></div>
            <div><dt>Organization</dt><dd>Zyora Labs</dd></div>
            <div><dt>Access level</dt><dd>Super admin</dd></div>
          </dl>
        </section>
    </Workspace>
  );
}