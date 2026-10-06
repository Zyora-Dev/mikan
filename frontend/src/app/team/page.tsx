import { redirect } from "next/navigation";
import { getTeamAccount } from "@/lib/team";
import TeamWorkspace from "./workspace";

export const metadata = { title: "Team Workspace | Mikan" };

export default async function TeamPage() {
  const account = await getTeamAccount();
  if (!account) redirect("/");
  return <TeamWorkspace account={account} />;
}