import { NextRequest } from "next/server";
import { adminManagementProxy } from "@/lib/admin-management-proxy";

async function forward(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  return adminManagementProxy(request, `/company-admins/${id}`);
}

export const PUT = forward;
export const DELETE = forward;