import "server-only";

import { cookies } from "next/headers";

export const adminBackend = process.env.ADMIN_API_URL || "http://127.0.0.1:8000";
export const adminCookie = "mikan_admin_session";

export type Admin = { id: number; email: string; name: string; role: "super_admin" };

export async function getAdmin(): Promise<Admin | null> {
  const token = (await cookies()).get(adminCookie)?.value;
  if (!token) return null;
  const response = await fetch(`${adminBackend}/auth/admin/me`, {
    headers: { Cookie: `${adminCookie}=${encodeURIComponent(token)}` },
    cache: "no-store",
    signal: AbortSignal.timeout(8000),
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error("Admin service unavailable.");
  return response.json();
}