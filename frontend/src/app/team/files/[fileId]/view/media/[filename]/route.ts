import type { NextRequest } from "next/server";
import { teamProxy } from "@/lib/team-proxy";

export async function GET(request: NextRequest, context: { params: Promise<{ fileId: string; filename: string }> }) {
  const { fileId } = await context.params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(fileId)) {
    return new Response(null, { status: 404, headers: { "Cache-Control": "private, no-store" } });
  }
  return teamProxy(request, `files/${fileId}/preview`, "team");
}