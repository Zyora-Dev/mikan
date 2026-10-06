import { notFound, redirect } from "next/navigation";
import { getCompanyAdmin } from "@/lib/company-admin";
import CompanyWorkspace from "../../../workspace";
import WorkflowRequest from "../../workflow-request";

export const metadata = { title: "Workflow Request | Mikan" };
export default async function RequestPage({ params }: { params: Promise<{ runId: string }> }) {
  const admin = await getCompanyAdmin();
  if (!admin) redirect("/company/admin/login");
  const { runId } = await params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)) notFound();
  return <CompanyWorkspace admin={admin} active="workflows"><WorkflowRequest identifier={runId} company /></CompanyWorkspace>;
}