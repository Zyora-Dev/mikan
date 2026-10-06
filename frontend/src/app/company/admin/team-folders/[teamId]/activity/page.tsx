import { notFound, redirect } from "next/navigation";
import { getCompanyAdmin } from "@/lib/company-admin";
import CompanyWorkspace from "../../../workspace";
import FolderActivity from "../../folder-activity";

export const metadata = { title: "Team Folder Activity | Mikan" };

export default async function TeamFolderActivityPage({ params }: { params: Promise<{ teamId: string }> }) {
  const admin = await getCompanyAdmin();
  if (!admin) redirect("/company/admin/login");
  const { teamId } = await params;
  if (!/^[1-9][0-9]{0,18}$/.test(teamId) || BigInt(teamId) > BigInt("9223372036854775807")) notFound();
  return <CompanyWorkspace admin={admin} active="folders"><FolderActivity key={teamId} teamId={teamId} /></CompanyWorkspace>;
}