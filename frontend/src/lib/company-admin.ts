import "server-only";
import { cookies } from "next/headers";
import { adminBackend } from "./admin";

export const companyAdminCookie = "mikan_company_admin_session";
export type CompanyAdmin = { id: number; name: string; email: string; role: "admin"; mobile: string; company_id: number; company_name: string };

export async function getCompanyAdmin(): Promise<CompanyAdmin | null> {
  const token = (await cookies()).get(companyAdminCookie)?.value;
  if (!token) return null;
  const response = await fetch(`${adminBackend}/auth/company/admin/me`, {
    headers: { Cookie: `${companyAdminCookie}=${encodeURIComponent(token)}` },
    cache: "no-store", signal: AbortSignal.timeout(8000),
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error("Company admin service unavailable.");
  return response.json();
}

export async function getCompanyPeopleSummary(): Promise<{ active: number; invited: number } | null> {
  const token = (await cookies()).get(companyAdminCookie)?.value;
  if (!token) return null;
  try {
    const response = await fetch(`${adminBackend}/company/teams/people?page_size=1`, { headers: { Cookie: `${companyAdminCookie}=${encodeURIComponent(token)}` }, cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!response.ok) return null;
    return (await response.json()).summary;
  } catch { return null; }
}