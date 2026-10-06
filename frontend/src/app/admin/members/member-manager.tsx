"use client";

import { useRouter } from "next/navigation";
import CompanyPicker from "../company-picker";
import TeamManager from "../../company/admin/teams/team-manager";
import styles from "../companies/companies.module.css";
import local from "../management.module.css";

export default function MemberManager({ company }: { company: string }) {
  const router = useRouter();
  return <>
    <div className={local.scope}><CompanyPicker value={company} onChange={value => router.replace(`/admin/members${value ? `?company=${value}` : ""}${window.location.hash || "#members"}`)} /></div>
    {company ? <TeamManager key={company} superAdmin endpoint={`/api/admin/management/companies/${company}/teams`} /> : <section className={styles.section}><div className={styles.heading}><h1>Members</h1></div><div className={styles.empty}>No company selected.</div></section>}
  </>;
}