import { notFound, redirect } from "next/navigation";
import { getTeamAccount } from "@/lib/team";
import TeamWorkspace from "../../../workspace";
import WorkflowRequest from "../../../../company/admin/workflows/workflow-request";

export const metadata = { title: "Workflow Request | Mikan" };
export default async function RequestPage({ params }: { params: Promise<{ runId: string }> }) {
  const account = await getTeamAccount();
  if (!account) redirect("/");
  const { runId } = await params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)) notFound();
  return <TeamWorkspace account={account}><WorkflowRequest identifier={runId} company={false} accountId={account.id} /></TeamWorkspace>;
}