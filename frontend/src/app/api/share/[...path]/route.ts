import type { NextRequest } from "next/server";
import { teamProxy } from "@/lib/team-proxy";

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return teamProxy(request, path.join("/"), "share");
}