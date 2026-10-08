import { NextRequest } from "next/server";
import { adminBackend, adminCookie } from "./admin";

export async function adminManagementProxy(request: NextRequest, path: string) {
  const headers = { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" };
  const fail = (detail: string, status: number) => Response.json({ detail }, { status, headers });
  const numeric = "[1-9][0-9]{0,18}";
  const teams = new RegExp(`^/admin/management/companies/${numeric}/teams$`).test(path);
  const people = new RegExp(`^/admin/management/companies/${numeric}/teams/people$`).test(path);
  const action = new RegExp(`^/admin/management/companies/${numeric}/teams/(invite|${numeric}/delete|people/${numeric}/(edit|resend|disable|delete))$`).test(path);
  const admin = new RegExp(`^/company-admins/${numeric}$`).test(path);
  const clear = /^\/admin\/management\/clear\/(preview|execute)$/.test(path);
  const allowed = request.method === "GET" ? teams || people : request.method === "POST" ? teams || action || clear : ["PUT", "DELETE"].includes(request.method) && admin;
  if (!allowed) return fail("Not found.", 404);
  const token = request.cookies.get(adminCookie)?.value;
  if (!token) return fail("Sign in required.", 401);
  const origin = request.headers.get("origin");
  if (request.method !== "GET") {
    const configured = process.env.ADMIN_PUBLIC_ORIGIN;
    const trusted = configured ? origin === configured : process.env.NODE_ENV === "development" && ["http://127.0.0.1:3000", "http://localhost:3000"].includes(origin || "");
    if (!trusted) return fail("Request origin not allowed.", 403);
    if (!request.headers.get("content-type")?.startsWith("application/json")) return fail("JSON required.", 415);
  }
  const maximum = 8192;
  if (Number(request.headers.get("content-length") || 0) > maximum) return fail("Request is too large.", 413);
  try {
    let body: Uint8Array<ArrayBuffer> | undefined;
    if (request.method !== "GET" && request.body) {
      const reader = request.body.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      while (true) {
        const result = await reader.read();
        if (result.done) break;
        length += result.value.byteLength;
        if (length > maximum) { await reader.cancel(); return fail("Request is too large.", 413); }
        chunks.push(result.value);
      }
      body = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
    }
    const upstream = await fetch(`${adminBackend}${path}${request.nextUrl.search}`, {
      method: request.method, body, cache: "no-store", signal: AbortSignal.timeout(clear ? 120000 : 20000),
      headers: { "Content-Type": "application/json", Cookie: `${adminCookie}=${encodeURIComponent(token)}`, ...(origin ? { Origin: origin } : {}) },
    });
    return new Response(await upstream.text(), { status: upstream.status, headers: { ...headers, "Content-Type": "application/json" } });
  } catch {
    return fail("Request interrupted. Refresh the preview or list to check the result before retrying.", 503);
  }
}