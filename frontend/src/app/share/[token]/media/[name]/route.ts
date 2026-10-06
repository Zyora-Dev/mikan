import type { NextRequest } from "next/server";
import { teamProxy } from "@/lib/team-proxy";

export async function GET(request: NextRequest, context: { params: Promise<{ token: string; name: string }> }) {
  const { token } = await context.params;
  return teamProxy(request, `${token}/preview`, "share");
}