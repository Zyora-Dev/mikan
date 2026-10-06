import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import Workspace from "../workspace";
import AdminManager from "./admin-manager";

export const metadata = { title: "Admins | Mikan" };

export default async function AdminsPage() {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  return <Workspace admin={admin} active="admins"><AdminManager /></Workspace>;
}