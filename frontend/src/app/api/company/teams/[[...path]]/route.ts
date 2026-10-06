import type { NextRequest } from "next/server";
import { teamProxy } from "@/lib/team-proxy";

async function forward(request: NextRequest, context: { params: Promise<{ path?: string[] }> }) {
  const { path = [] } = await context.params;
  return teamProxy(request, path.join("/"), "company");
}

export const GET = forward;
export const POST = forward;