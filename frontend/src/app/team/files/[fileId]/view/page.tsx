import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { adminBackend } from "@/lib/admin";
import { getTeamAccount, teamCookie } from "@/lib/team";
import { FilePreviewPage } from "../../media-preview";

export const metadata = { robots: { index: false, follow: false } };

export default async function PreviewPage({ params }: { params: Promise<{ fileId: string }> }) {
  if (!await getTeamAccount()) redirect("/");
  const { fileId } = await params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(fileId)) notFound();
  const token = (await cookies()).get(teamCookie)?.value;
  if (!token) redirect("/");
  const response = await fetch(`${adminBackend}/team/files/${fileId}/preview`, {
    headers: { Cookie: `${teamCookie}=${encodeURIComponent(token)}`, Range: "bytes=0-0", "Accept-Encoding": "identity" },
    cache: "no-store", redirect: "error", signal: AbortSignal.timeout(20000),
  });
  await response.body?.cancel();
  if (response.status === 401) redirect("/");
  if (response.status === 404 || response.status === 415) notFound();
  if (response.status !== 206) throw new Error("File preview is temporarily unavailable.");
  const filename = response.headers.get("content-disposition")?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const size = response.headers.get("content-range")?.match(/^bytes 0-0\/([0-9]+)$/)?.[1];
  if (!filename || !size) throw new Error("File preview is temporarily unavailable.");
  return <FilePreviewPage file={{ id: fileId, name: decodeURIComponent(filename), size_bytes: Number(size), state: "ready" }} />;
}