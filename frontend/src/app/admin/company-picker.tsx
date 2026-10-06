"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./companies/companies.module.css";
import local from "./admins/admins.module.css";

export default function CompanyPicker({ value, onChange, all = false, disabled = false }: { value: string; onChange: (value: string, name: string) => void; all?: boolean; disabled?: boolean }) {
  const router = useRouter();
  const [companies, setCompanies] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true); setError("");
      try {
        const items: { id: number; name: string }[] = [];
        for (let page = 1; !controller.signal.aborted; page++) {
          const response = await fetch(`/api/admin/companies?page_size=100&page=${page}`, { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) });
          if (response.status === 401) { router.replace("/admin/login"); return; }
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail || "Unable to load companies.");
          items.push(...data.items);
          if (!data.items.length || items.length >= data.total) break;
        }
        if (!controller.signal.aborted) setCompanies(items.sort((first, second) => first.name.localeCompare(second.name)));
      } catch (failure) { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to load companies."); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [revision, router]);
  return <div>
    <label className={styles.field}>Company<select className={local.companySelect} value={value} disabled={disabled || loading || !!error} onChange={event => onChange(event.target.value, event.target.selectedOptions[0].text)}><option value="">{loading ? "Loading companies..." : "Select company"}</option>{all && <option value="all">All companies</option>}{companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}</select></label>
    {error && <div role="alert"><p>{error}</p><button className={styles.secondary} disabled={disabled} onClick={() => setRevision(value => value + 1)}>Retry</button></div>}
    {!loading && !error && !companies.length && <p>No companies yet. <Link href="/admin/companies">Add company</Link></p>}
  </div>;
}