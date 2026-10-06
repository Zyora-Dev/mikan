import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import Workspace from "../../workspace";
import ZeptoMailSettings from "../zeptomail-settings";

export const metadata = { title: "ZeptoMail | Mikan" };

export default async function ZeptoMailPage() {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  return <Workspace admin={admin} active="integrations"><ZeptoMailSettings view="details" /></Workspace>;
}