import { redirect } from "next/navigation";
import { getTeamAccount } from "@/lib/team";
import TeamWorkspace from "../workspace";
import FileManager from "./file-manager";

export const metadata = { title: "My Files | Mikan" };
export default async function FilesPage({ searchParams }: { searchParams: Promise<{ view?: string | string[]; folder?: string | string[]; scope?: string | string[] }> }) {
  const account = await getTeamAccount();
  if (!account) redirect("/");
  const { view, folder, scope } = await searchParams;
  const sharedWithMe = scope === "shared";
  const trash = scope === "trash";
  const currentFolder = !sharedWithMe && !trash && typeof folder === "string" ? folder : "";
  return <TeamWorkspace account={account} trash={trash}><FileManager key={`${sharedWithMe}:${trash}:${currentFolder}`} owner={`${account.company_id}:${account.team_id}:${account.id}`} currentFolder={currentFolder} sharedWithMe={sharedWithMe} trash={trash} view={view === "list" || view === "compact" ? view : "grid"} /></TeamWorkspace>;
}