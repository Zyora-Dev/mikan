import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowUpRight, BarChart3, Bell, Building2, ClipboardList, LayoutDashboard, MessageCircle, Plug, ShieldCheck, Trash2, UserRound, Users } from "lucide-react";
import { NotificationBell } from "../notifications";
import type { Admin } from "@/lib/admin";
import Logout from "./logout";
import styles from "./admin.module.css";

const navigation = [
  { key: "notifications", label: "Notifications", href: "/admin/notifications", icon: Bell, roles: ["super_admin"] },
  { key: "overview", label: "Overview", href: "/admin", icon: LayoutDashboard, roles: ["super_admin"] },
  { key: "companies", label: "Companies", href: "/admin/companies", icon: Building2, roles: ["super_admin"] },
  { key: "admins", label: "Admins", href: "/admin/admins", icon: Users, roles: ["super_admin"] },
  { key: "members", label: "Members", href: "/admin/members", icon: UserRound, roles: ["super_admin"] },
  { key: "clear", label: "Clear Data", href: "/admin/clear-data", icon: Trash2, roles: ["super_admin"] },
  { key: "integrations", label: "Integrations", href: "/admin/integrations", icon: Plug, roles: ["super_admin"] },
  { key: "whatsapp", label: "WhatsApp", href: "/admin/whatsapp", icon: MessageCircle, roles: ["super_admin"] },
  { key: "audit", label: "Audit Logs", href: "/admin/audit", icon: ClipboardList, roles: ["super_admin"] },
  { key: "reports", label: "Reports", href: "/admin/reports", icon: BarChart3, roles: ["super_admin"] },
];

export default function Workspace({ admin, active, children }: { admin: Admin; active: "overview" | "companies" | "admins" | "members" | "clear" | "integrations" | "whatsapp" | "audit" | "reports" | "notifications"; children: ReactNode }) {
  return (
    <div className={styles.shell}>
      <a href="#admin-content" className={styles.skipLink}>Skip to content</a>
      <aside className={styles.sidebar} aria-label="Zyora Labs workspace">
        <Link href="/admin" className={styles.brand} aria-label="Mikan overview">
          <Image src="/mikan-logo.jpg" alt="Mikan Engineering" width={104} height={42} preload />
        </Link>
        <nav className={styles.navigation} aria-label="Super admin navigation">
          <p>WORKSPACE</p>
          {navigation.filter(item => item.roles.includes(admin.role)).map(item => (
            <Link key={item.key} href={item.href} aria-current={item.key === active ? "page" : undefined} className={styles.navItem}>
              <item.icon size={18} /><span>{item.label}</span>
            </Link>
          ))}
        </nav>
        <div className={styles.sidebarBottom}>
          <div className={styles.identity}><span className={styles.avatar}>{admin.name.slice(0, 1).toUpperCase()}</span><span><strong>{admin.name}</strong><span>Super admin</span></span></div>
          <Logout />
        </div>
      </aside>
      <main className={styles.page} id="admin-content" tabIndex={-1}>
        <header className={styles.header}><span>Workspace <span className={styles.breadcrumb}>/</span> <strong>{navigation.find(item => item.key === active)?.label}</strong></span><div className={styles.headerActions}><NotificationBell scope="admin" /><div className={styles.access}><ShieldCheck size={17} /><span><strong>Zyora Labs</strong><span>Super admin</span></span></div></div></header>
        {children}
        <footer className={styles.footer}><span>Zyora Labs Console</span><a href="https://zyoralabs.com" target="_blank" rel="noopener noreferrer">zyoralabs.com <ArrowUpRight size={13} /></a></footer>
      </main>
    </div>
  );
}