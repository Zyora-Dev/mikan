import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import Workspace from "../workspace";
import CompanyManager from "./company-manager";

export const metadata = { title: "Companies | Mikan" };

export default async function CompaniesPage() {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  return <Workspace admin={admin} active="companies"><CompanyManager /></Workspace>;
}