import type { NextRequest } from "next/server";
import { insightsProxy } from "@/lib/insights-proxy";

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return insightsProxy(request, (await context.params).path, false);
}