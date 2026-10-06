import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import Workspace from "../workspace";
import WhatsAppModule from "./whatsapp-module";

export const metadata = { title: "WhatsApp | Mikan" };

export default async function WhatsAppPage() {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  return <Workspace admin={admin} active="whatsapp"><WhatsAppModule /></Workspace>;
}