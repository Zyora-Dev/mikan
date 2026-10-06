import type { NextRequest } from "next/server";
import { adminBackend, adminCookie } from "./admin";
import { companyAdminCookie } from "./company-admin";
import { teamCookie } from "./team";

export async function notificationProxy(request: NextRequest, path: string[], scope: "admin" | "company" | "team") {
  const headers = { "Content-Type": "application/json", "Cache-Control": "private, no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox" };
  const fail = (detail: string, status: number) => Response.json({ detail }, { status, headers });
  const endpoint = path.join("/");
  const streaming = endpoint === "stream" && request.method === "GET";
  const allowed = request.method === "GET" ? /^(|unread|stream)$/.test(endpoint) : request.method === "POST" && /^(read-all|(?:storage|workflow):[1-9][0-9]{0,18}\/read)$/.test(endpoint);
  if (!allowed) return fail("Not found.", 404);
  const cookie = { admin: adminCookie, company: companyAdminCookie, team: teamCookie }[scope];
  const token = request.cookies.get(cookie)?.value;
  if (!token || !/^[A-Za-z0-9_-]{32,128}$/.test(token)) return fail("Sign in required.", 401);
  const origin = request.headers.get("origin");
  const configured = process.env.ADMIN_PUBLIC_ORIGIN;
  if (request.method === "POST") {
    const trusted = configured ? origin === configured : process.env.NODE_ENV === "development" && ["http://127.0.0.1:3000", "http://localhost:3000"].includes(origin || "");
    if (!trusted) return fail("Request origin not allowed.", 403);
    if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") return fail("JSON required.", 415);
    if (Number(request.headers.get("content-length") || 0) > 1024) return fail("Request too large.", 413);
  }
  try {
    let body: string | undefined;
    if (request.method === "POST") {
      const reader = request.body?.getReader();
      if (!reader) return fail("JSON required.", 422);
      const decoder = new TextDecoder();
      let length = 0;
      body = "";
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        length += chunk.value.byteLength;
        if (length > 1024) { await reader.cancel(); return fail("Request too large.", 413); }
        body += decoder.decode(chunk.value, { stream: true });
      }
      body += decoder.decode();
      try { JSON.parse(body); } catch { return fail("Valid JSON required.", 422); }
    }
    const query = new URLSearchParams();
    for (const key of ["page", "search", "view", "kind", "from_date", "to_date"]) {
      const value = request.nextUrl.searchParams.get(key);
      if (value !== null) query.set(key, value);
    }
    const upstream = await fetch(`${adminBackend}/${scope}/notifications${endpoint ? `/${endpoint}` : ""}?${query}`, {
      method: request.method, body, cache: "no-store", redirect: "manual", signal: AbortSignal.any([request.signal, AbortSignal.timeout(streaming ? 130000 : 20000)]),
      headers: { Cookie: `${cookie}=${token}`, "Content-Type": "application/json", ...(origin ? { Origin: origin } : {}) },
    });
    if (upstream.status >= 300 && upstream.status < 400) { await upstream.body?.cancel(); return fail("Unexpected service response.", 502); }
    if (upstream.status >= 500) { await upstream.body?.cancel(); return fail("Notifications unavailable. Please try again.", 503); }
    if (streaming && upstream.ok) {
      if (upstream.headers.get("content-type")?.split(";")[0] !== "text/event-stream") { await upstream.body?.cancel(); return fail("Unexpected service response.", 502); }
      return new Response(upstream.body, { status: upstream.status, headers: { ...headers, "Content-Type": "text/event-stream", "X-Accel-Buffering": "no" } });
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch { return fail("Notifications unavailable. Please try again.", 503); }
}