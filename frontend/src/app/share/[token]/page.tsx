import { cookies } from "next/headers";
import { notFound } from "next/navigation";
import { LockKeyhole } from "lucide-react";
import { adminBackend } from "@/lib/admin";
import { teamCookie } from "@/lib/team";
import { SharedFilePage, type PreviewFile } from "../../team/files/media-preview";
import drive from "../../team/files/files.module.css";

export const metadata = { title: "Shared file | Mikan", robots: { index: false, follow: false }, referrer: "no-referrer" as const };

export default async function SharedFile({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  if (!/^[0-9a-f]{64}$/.test(token)) notFound();
  const session = (await cookies()).get(teamCookie)?.value;
  let response: Response;
  try {
    response = await fetch(`${adminBackend}/team/files/link/${token}`, {
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(20000),
      headers: session && /^[A-Za-z0-9_-]{32,128}$/.test(session) ? { Cookie: `${teamCookie}=${session}` } : {},
    });
  } catch {
    return <main className={drive.sharedPage}><header><strong>Mikan Cloud</strong></header><section><h1>File temporarily unavailable</h1><a href={`/share/${token}`}>Try again</a></section></main>;
  }
  if (response.status === 404) notFound();
  if (!response.ok) {
    const restricted = response.status === 401 || response.status === 403;
    return <main className={drive.sharedPage}><header><strong>Mikan Cloud</strong></header><section><LockKeyhole size={36} /><h1>{response.status === 401 ? "Sign in to open this file" : response.status === 403 ? "You do not have access" : "File temporarily unavailable"}</h1>{restricted ? <><p>{response.status === 403 ? "Ask the file owner to grant access to your account." : "This file is restricted to members with access."}</p>{response.status === 401 && <a href={`/?next=${encodeURIComponent(`/share/${token}`)}`}>Sign in</a>}</> : <a href={`/share/${token}`}>Try again</a>}</section></main>;
  }
  const file: PreviewFile = await response.json();
  return <SharedFilePage file={file} token={token} />;
}