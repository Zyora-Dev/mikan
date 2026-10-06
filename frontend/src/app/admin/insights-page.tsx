import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import { getCompanyAdmin } from "@/lib/company-admin";
import Workspace from "./workspace";
import CompanyWorkspace from "../company/admin/workspace";
import InsightsManager from "./insights-manager";

export type InsightsParams = Promise<{ view?: string | string[] }>;

export default async function InsightsPage({ company, audit, searchParams }: { company: boolean; audit: boolean; searchParams: InsightsParams }) {
  const { view: requested } = await searchParams;
  const views = audit ? ["all", "data", "team_folders", "workflows"] : ["storage-teams", "storage-employees", "largest-files", "activity", "workflows", "automation"];
  const view = typeof requested === "string" && views.includes(requested) ? requested : views[0];
  const content = <InsightsManager key={view} company={company} audit={audit} view={view} />;
  const active = audit ? "audit" : "reports";
  if (company) {
    const admin = await getCompanyAdmin();
    if (!admin) redirect("/company/admin/login");
    return <CompanyWorkspace admin={admin} active={active}>{content}</CompanyWorkspace>;
  }
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  return <Workspace admin={admin} active={active}>{content}</Workspace>;
}