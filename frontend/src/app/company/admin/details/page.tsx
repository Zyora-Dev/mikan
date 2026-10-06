import { redirect } from "next/navigation";
import { getCompanyAdmin } from "@/lib/company-admin";
import CompanyWorkspace from "../workspace";
import CompanyProfile from "./company-profile";

export const metadata = { title: "Company Details | Mikan" };

export default async function CompanyDetailsPage() {
  const admin = await getCompanyAdmin();
  if (!admin) redirect("/company/admin/login");
  return <CompanyWorkspace admin={admin} active="company"><CompanyProfile /></CompanyWorkspace>;
}