import type { NextRequest } from "next/server";
import { notificationProxy } from "@/lib/notification-proxy";

async function handle(request: NextRequest, context: { params: Promise<{ path?: string[] }> }) {
  return notificationProxy(request, (await context.params).path || [], "team");
}
export { handle as GET, handle as POST };