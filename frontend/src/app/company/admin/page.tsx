import Image from "next/image";
import Link from "next/link";
import { redirect } from "next/navigation";
import { ArrowRight, ArrowUpRight, Building2, HardDrive, Mail, Phone, ShieldCheck, UserRound, Users } from "lucide-react";
import { getCompanyAdmin, getCompanyPeopleSummary } from "@/lib/company-admin";
import CompanyWorkspace from "./workspace";
import StorageDashboard from "./storage-dashboard";
import styles from "./dashboard.module.css";

export const metadata = { title: "Company Dashboard | Mikan" };

export default async function CompanyAdminPage() {
  const admin = await getCompanyAdmin();
  if (!admin) redirect("/company/admin/login");
  const people = await getCompanyPeopleSummary();
  const initial = admin.name.slice(0, 1).toUpperCase();
  return <CompanyWorkspace admin={admin} active="dashboard">
      <section className={styles.content}>
        <div className={styles.titleRow}><h1>Dashboard</h1><span className={styles.breadcrumb}>Workspace / Dashboard</span></div>
        <div className={styles.overviewHeading}><h2>Overview</h2><span className={styles.active}><span />Account active</span></div>
        <div className={styles.summary}>
          <article className={`${styles.summaryItem} ${styles.companyCard}`}>
            <div className={styles.summaryLabel}><h3>Company details</h3><span className={styles.cardIcon}><Building2 size={20} /></span></div>
            <p className={styles.summaryValue}>{admin.company_name}</p>
            <dl className={styles.cardDetails}><div><dt>Administrator</dt><dd>{admin.name}</dd></div></dl>
            <Link href="/company/admin/details" className={styles.detailsLink}>View company details<ArrowUpRight size={14} /></Link>
            <div className={styles.cardFooter}><Mail size={15} /><a href={`mailto:${admin.email}`} aria-label={`Email administrator ${admin.email}`}>{admin.email}</a></div>
          </article>
          <article className={`${styles.summaryItem} ${styles.membersCard}`}>
            <div className={styles.summaryLabel}><h3>Members</h3><span className={styles.cardIcon}><Users size={20} /></span></div>
            <p className={styles.statValue}>{people ? people.active : "—"}</p>
            <p className={styles.statCaption}>Active team accounts</p>
            <p className={styles.summaryNote}>{people ? `${people.invited} pending invitations` : "Member data unavailable"}</p>
            <div className={styles.cardFooter}><Link className={styles.cardButton} href="/company/admin/teams#members">View members<ArrowRight size={16} /></Link></div>
          </article>
          <article className={`${styles.summaryItem} ${styles.storageCard}`}>
            <div className={styles.summaryLabel}><h3>Storage</h3><span className={styles.cardIcon}><HardDrive size={20} /></span></div>
            <p className={styles.statValue}>16<span>TB</span></p>
            <p className={styles.statCaption}>Total capacity</p>
            <p className={styles.summaryNote}>Company storage</p>
            <div className={styles.cardFooter}><span>Storage used</span><strong>Not available</strong></div>
          </article>
        </div>
        <StorageDashboard />
        <div className={styles.detailGrid}>
          <section className={styles.account} id="account-details" aria-labelledby="account-heading" tabIndex={-1}>
            <div className={styles.sectionHeading}><div><h2 id="account-heading">Account details</h2><p>Administrator profile</p></div><UserRound size={20} /></div>
            <div className={styles.profile}><span className={styles.profileAvatar}>{initial}</span><div><h3>{admin.name}</h3><span>Company admin</span></div><span className={styles.active}><span />Active</span></div>
            <dl className={styles.details}>
              <div><dt>Full name</dt><dd>{admin.name}</dd></div>
              <div><dt>Company</dt><dd>{admin.company_name}</dd></div>
              <div><dt>Email address</dt><dd><a href={`mailto:${admin.email}`}><Mail size={16} /><span>{admin.email}</span><ArrowUpRight size={14} /></a></dd></div>
              <div><dt>Mobile number</dt><dd><a href={`tel:${admin.mobile}`}><Phone size={16} /><span>{admin.mobile}</span><ArrowUpRight size={14} /></a></dd></div>
              <div><dt>Access level</dt><dd>Company admin</dd></div>
            </dl>
          </section>
          <aside className={styles.companyPanel} aria-labelledby="company-heading">
            <div className={styles.sectionHeading}><h2 id="company-heading">Company workspace</h2><Building2 size={20} /></div>
            <div className={styles.companyImage}><Image src="/architecture.jpg" alt="Architectural facade of a modern office building" fill sizes="(max-width: 760px) 100vw, (max-width: 1100px) 45vw, 340px" /></div>
            <p className={styles.companyEyebrow}>MIKAN ENGINEERING</p><h3>{admin.company_name}</h3>
            <div className={styles.companyAccess}><ShieldCheck size={18} /><span>Company administrator access</span></div>
          </aside>
        </div>
      </section>
  </CompanyWorkspace>;
}