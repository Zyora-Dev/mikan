import { NextRequest } from "next/server";
import { adminManagementProxy } from "@/lib/admin-management-proxy";

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return adminManagementProxy(request, `/admin/management/${path.join("/")}`);
}

export const GET = forward;
export const POST = forward;