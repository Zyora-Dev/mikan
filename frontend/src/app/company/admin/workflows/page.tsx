import { redirect } from "next/navigation";
import { getCompanyAdmin } from "@/lib/company-admin";
import CompanyWorkspace from "../workspace";
import WorkflowList from "./workflow-list";

export const metadata = { title: "Workflows | Mikan" };
export default async function WorkflowsPage({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const admin = await getCompanyAdmin();
  if (!admin) redirect("/company/admin/login");
  const requested = (await searchParams).view;
  const view = requested === "runs" || requested === "jobs" || requested === "uploads" ? requested : "definitions";
  return <CompanyWorkspace admin={admin} active="workflows"><WorkflowList key={view} company view={view} /></CompanyWorkspace>;
}