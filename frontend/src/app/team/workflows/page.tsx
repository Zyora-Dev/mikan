import { redirect } from "next/navigation";
import { getTeamAccount } from "@/lib/team";
import TeamWorkspace from "../workspace";
import WorkflowList, { type WorkflowView } from "../../company/admin/workflows/workflow-list";

export const metadata = { title: "My Workflows | Mikan" };
export default async function TeamWorkflowsPage({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const account = await getTeamAccount();
  if (!account) redirect("/");
  const requested = (await searchParams).view;
  const view: WorkflowView = requested === "mine" || requested === "inbox" || requested === "notifications" ? requested : "available";
  return <TeamWorkspace account={account}><WorkflowList key={view} company={false} view={view} /></TeamWorkspace>;
}