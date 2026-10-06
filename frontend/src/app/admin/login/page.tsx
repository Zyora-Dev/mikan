import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import LoginForm from "./login-form";

export const metadata: Metadata = { title: "Super Admin Sign In | Mikan" };

export default async function AdminLogin() {
  const admin = await getAdmin().catch(() => null);
  if (admin) redirect("/admin");
  return <LoginForm />;
}