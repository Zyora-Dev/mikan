import { notFound, redirect } from "next/navigation";
import { getTeamAccount } from "@/lib/team";
import TeamWorkspace from "../../../workspace";
import WorkflowSubmission from "../../workflow-submission";

export const metadata = { title: "Submit File | Mikan" };
export default async function SubmissionPage({ params }: { params: Promise<{ workflowId: string }> }) {
  const account = await getTeamAccount();
  if (!account) redirect("/");
  const { workflowId } = await params;
  if (!/^[1-9][0-9]{0,15}$/.test(workflowId) || !Number.isSafeInteger(Number(workflowId))) notFound();
  return <TeamWorkspace account={account}><WorkflowSubmission identifier={workflowId} /></TeamWorkspace>;
}