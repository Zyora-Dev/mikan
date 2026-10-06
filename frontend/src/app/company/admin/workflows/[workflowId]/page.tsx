import { notFound, redirect } from "next/navigation";
import { getCompanyAdmin } from "@/lib/company-admin";
import CompanyWorkspace from "../../workspace";
import WorkflowEditor from "../workflow-editor";

export const metadata = { title: "Workflow Editor | Mikan" };

export default async function WorkflowEditorPage({ params }: { params: Promise<{ workflowId: string }> }) {
  const admin = await getCompanyAdmin();
  if (!admin) redirect("/company/admin/login");
  const { workflowId } = await params;
  if (workflowId !== "new" && (!/^[1-9][0-9]{0,15}$/.test(workflowId) || !Number.isSafeInteger(Number(workflowId)))) notFound();
  return <CompanyWorkspace admin={admin} active="workflows"><WorkflowEditor key={workflowId} identifier={workflowId} /></CompanyWorkspace>;
}