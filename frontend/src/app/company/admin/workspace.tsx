import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import { BarChart3, Bell, Building2, ClipboardList, Database, FolderClosed, GitBranch, LayoutDashboard, ShieldCheck, UserRound, Users } from "lucide-react";
import { NotificationBell } from "../../notifications";
import type { CompanyAdmin } from "@/lib/company-admin";
import Logout from "../../admin/logout";
import styles from "./dashboard.module.css";

export default function CompanyWorkspace({ admin, active, children }: { admin: CompanyAdmin; active: "dashboard" | "company" | "teams" | "folders" | "workflows" | "data" | "audit" | "reports" | "notifications"; children: ReactNode }) {
  const initial = admin.name.slice(0, 1).toUpperCase();
  return <div className={styles.shell}>
    <a href="#company-content" className={styles.skipLink}>Skip to content</a>
    <aside className={styles.sidebar} aria-label="Company workspace">
      <Link href="/company/admin" className={styles.brand} aria-label="Company dashboard"><Image src="/mikan-logo.jpg" alt="Mikan Engineering" width={104} height={42} preload /></Link>
      <nav className={styles.navigation} aria-label="Company admin navigation">
        <p>WORKSPACE</p>
        <Link href="/company/admin" className={styles.navItem} aria-current={active === "dashboard" ? "page" : undefined}><LayoutDashboard size={18} /><span>Dashboard</span></Link>
        <Link href="/company/admin/details" className={styles.navItem} aria-current={active === "company" ? "page" : undefined}><Building2 size={18} /><span>Company details</span></Link>
        {active === "teams" ? <a href="#teams" className={styles.navItem} aria-current="page"><Users size={18} /><span>Teams</span></a> : <Link href="/company/admin/teams#teams" className={styles.navItem}><Users size={18} /><span>Teams</span></Link>}
        <Link href="/company/admin/team-folders" className={styles.navItem} aria-current={active === "folders" ? "page" : undefined}><FolderClosed size={18} /><span>Team Folders</span></Link>
        <Link href="/company/admin/data" className={styles.navItem} aria-current={active === "data" ? "page" : undefined}><Database size={18} /><span>Data Administration</span></Link>
        <Link href="/company/admin/workflows" className={styles.navItem} aria-current={active === "workflows" ? "page" : undefined}><GitBranch size={18} /><span>Workflows</span></Link>
        <Link href="/company/admin/audit" className={styles.navItem} aria-current={active === "audit" ? "page" : undefined}><ClipboardList size={18} /><span>Audit Logs</span></Link>
        <Link href="/company/admin/reports" className={styles.navItem} aria-current={active === "reports" ? "page" : undefined}><BarChart3 size={18} /><span>Reports</span></Link>
        <Link href="/company/admin/notifications" className={styles.navItem} aria-current={active === "notifications" ? "page" : undefined}><Bell size={18} /><span>Notifications</span></Link>
        <Link href="/company/admin#account-details" className={styles.navItem}><UserRound size={18} /><span>My account</span></Link>
      </nav>
      <div className={styles.sidebarBottom}><div className={styles.identity}><span className={styles.avatar}>{initial}</span><div><strong>{admin.name}</strong><span>Company admin</span></div></div><Logout company /></div>
    </aside>
    <main className={styles.page} id="company-content" tabIndex={-1}>
      <header className={styles.header}>
        <div className={styles.welcome}><strong>Welcome back, {admin.name}</strong><span>{admin.company_name}</span></div>
        <div className={styles.headerActions}><NotificationBell scope="company" /><Link href="/company/admin#account-details" className={styles.headerAccount} aria-label="View my account"><ShieldCheck size={18} /><span>Company admin</span><span className={styles.headerAvatar}>{initial}</span></Link></div>
      </header>
      {children}
      <footer className={styles.footer}><span>Mikan Engineering</span><a href="https://zyoralabs.com" target="_blank" rel="noopener noreferrer">Powered by Zyora Labs</a></footer>
    </main>
  </div>;
}