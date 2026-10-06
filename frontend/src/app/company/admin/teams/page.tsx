import { redirect } from "next/navigation";
import { getCompanyAdmin } from "@/lib/company-admin";
import CompanyWorkspace from "../workspace";
import TeamManager from "./team-manager";

export const metadata = { title: "Teams | Mikan" };

export default async function TeamsPage() {
  const admin = await getCompanyAdmin();
  if (!admin) redirect("/company/admin/login");
  return <CompanyWorkspace admin={admin} active="teams"><TeamManager /></CompanyWorkspace>;
}