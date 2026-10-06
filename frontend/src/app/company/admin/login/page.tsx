import { redirect } from "next/navigation";
import { getCompanyAdmin } from "@/lib/company-admin";
import LoginForm from "./login-form";

export const metadata = { title: "Company Admin Sign In | Mikan" };

export default async function CompanyLoginPage() {
  if (await getCompanyAdmin()) redirect("/company/admin");
  return <LoginForm />;
}