import { notFound, redirect } from "next/navigation";
import { getAdmin } from "@/lib/admin";
import Workspace from "../../../workspace";
import StorageSettings from "../storage-settings";

export const metadata = { title: "Storage Configuration | Mikan" };

export default async function StorageConfigurationPage({ params }: { params: Promise<{ provider: string }> }) {
  const admin = await getAdmin();
  if (!admin) redirect("/admin/login");
  const { provider } = await params;
  if (provider !== "r2" && provider !== "s3") notFound();
  return <Workspace admin={admin} active="integrations"><StorageSettings key={provider} provider={provider} /></Workspace>;
}