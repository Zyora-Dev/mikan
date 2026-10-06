import { redirect } from "next/navigation";
import { getCompanyAdmin } from "@/lib/company-admin";
import CompanyWorkspace from "../workspace";
import DataManager from "./data-manager";

export const metadata = { title: "Data Administration | Mikan" };

export default async function DataPage({ searchParams }: { searchParams: Promise<{ view?: string | string[] }> }) {
  const admin = await getCompanyAdmin();
  if (!admin) redirect("/company/admin/login");
  const { view: requestedView } = await searchParams;
  const view = requestedView === "folders" || requestedView === "trash" || requestedView === "activity" ? requestedView : "files";
  return <CompanyWorkspace admin={admin} active="data"><DataManager view={view} /></CompanyWorkspace>;
}