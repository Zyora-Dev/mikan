import type { NextRequest } from "next/server";
import { adminBackend, adminCookie } from "./admin";
import { companyAdminCookie } from "./company-admin";

const privateHeaders = { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox" };

export async function insightsProxy(request: NextRequest, path: string[], company: boolean) {
  const endpoint = path.join("/");
  const fail = (detail: string, status: number) => Response.json({ detail }, { status, headers: privateHeaders });
  if (request.method !== "GET" || !(company && endpoint === "storage") && !/^(audit|reports\/(storage-teams|storage-employees|largest-files|activity|workflows|automation))$/.test(endpoint)) return fail("Not found.", 404);
  const cookie = company ? companyAdminCookie : adminCookie;
  const token = request.cookies.get(cookie)?.value;
  if (!token || token.length > 128) return fail("Sign in required.", 401);
  const query = new URLSearchParams();
  for (const key of ["page", "search", "from_date", "to_date", "export", "sort", ...(endpoint === "audit" ? ["source", "action"] : []), ...(!company ? ["company_id"] : [])]) {
    const value = request.nextUrl.searchParams.get(key);
    if (value !== null) query.set(key, value);
  }
  try {
    const upstream = await fetch(`${adminBackend}/${company ? "company/teams" : "admin"}/insights/${endpoint}?${query}`, {
      headers: { Cookie: `${cookie}=${encodeURIComponent(token)}` }, cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(30000),
    });
    if (upstream.status >= 300 && upstream.status < 400) return fail("Unexpected service response.", 502);
    if (upstream.status >= 500) return fail("Reporting service unavailable. Please try again.", 503);
    const headers = new Headers(privateHeaders);
    for (const name of ["content-type", "content-disposition"]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch {
    return fail("Reporting service unavailable. Please try again.", 503);
  }
}