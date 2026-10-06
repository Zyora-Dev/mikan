import { redirect } from "next/navigation";
import { getTeamAccount } from "@/lib/team";
import TeamWorkspace from "../workspace";
import FileManager from "../files/file-manager";

export const metadata = { title: "Team Drives | Mikan" };

export default async function TeamDrivePage({ searchParams }: { searchParams: Promise<{ owner?: string; folder?: string; view?: string }> }) {
  const account = await getTeamAccount();
  if (!account) redirect("/");
  if (account.role !== "manager") redirect("/team/files");
  const { owner, folder = "", view } = await searchParams;
  if (typeof owner !== "string" || !/^[1-9][0-9]{0,18}$/.test(owner) || BigInt(owner) > BigInt("9223372036854775807")) redirect("/team#team-members");
  const currentFolder = typeof folder === "string" ? folder : "";
  return <TeamWorkspace account={account}><FileManager key={`${owner}:${currentFolder}`} owner={`${account.company_id}:${account.team_id}:${account.id}`} driveOwner={owner} currentFolder={currentFolder} view={view === "list" || view === "compact" ? view : "grid"} /></TeamWorkspace>;
}