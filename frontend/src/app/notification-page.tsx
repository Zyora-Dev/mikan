import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import { getCompanyAdmin } from "@/lib/company-admin";
import { getTeamAccount } from "@/lib/team";
import Workspace from "./admin/workspace";
import CompanyWorkspace from "./company/admin/workspace";
import TeamWorkspace from "./team/workspace";
import NotificationCenter, { type NotificationScope } from "./notifications";

export type NotificationParams = Promise<{ view?: string | string[] }>;
export default async function NotificationPage({ scope, searchParams }: { scope: NotificationScope; searchParams: NotificationParams }) {
  const requested = (await searchParams).view;
  const view = requested === "read" || requested === "unread" ? requested : "all";
  const content = <NotificationCenter key={`${scope}:${view}`} scope={scope} view={view} />;
  if (scope === "company") {
    const admin = await getCompanyAdmin();
    if (!admin) redirect("/company/admin/login");
    return <CompanyWorkspace admin={admin} active="notifications">{content}</CompanyWorkspace>;
  }
  if (scope === "team") {
    const account = await getTeamAccount();
    if (!account) redirect("/");
    return <TeamWorkspace account={account}>{content}</TeamWorkspace>;
  }
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  return <Workspace admin={admin} active="notifications">{content}</Workspace>;
}