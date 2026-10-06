import { redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import Workspace from "../workspace";
import ClearData from "./clear-data";

export const metadata = { title: "Clear Data | Mikan" };

export default async function ClearDataPage() {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  return <Workspace admin={admin} active="clear"><ClearData /></Workspace>;
}