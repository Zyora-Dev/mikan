import { redirect } from "next/navigation";
import { getCompanyAdmin } from "@/lib/company-admin";
import CompanyWorkspace from "../workspace";
import FolderManager from "./folder-manager";

export const metadata = { title: "Team Folders | Mikan" };

export default async function TeamFoldersPage() {
  const admin = await getCompanyAdmin();
  if (!admin) redirect("/company/admin/login");
  return <CompanyWorkspace admin={admin} active="folders"><FolderManager /></CompanyWorkspace>;
}