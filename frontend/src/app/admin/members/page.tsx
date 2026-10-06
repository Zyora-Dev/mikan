import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import Workspace from "../workspace";
import MemberManager from "./member-manager";

export const metadata = { title: "Members | Mikan" };

export default async function MembersPage({ searchParams }: { searchParams: Promise<{ company?: string }> }) {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  const { company = "" } = await searchParams;
  const selected = /^[1-9][0-9]{0,15}$/.test(company) && Number.isSafeInteger(Number(company)) ? company : "";
  return <Workspace admin={admin} active="members"><MemberManager company={selected} /></Workspace>;
}