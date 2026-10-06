import { NextRequest } from "next/server";
import { adminBackend } from "@/lib/admin";
import { companyAdminCookie } from "@/lib/company-admin";

async function forward(request: NextRequest, context: { params: Promise<{ action: string }> }) {
  const { action } = await context.params;
  const headers = new Headers({ "Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  const fail = (detail: string, status: number) => Response.json({ detail }, { status, headers });
  if (!(request.method === "GET" ? action === "me" : ["login", "logout"].includes(action))) return fail("Not found.", 404);
  const origin = request.headers.get("origin");
  const configured = process.env.ADMIN_PUBLIC_ORIGIN;
  const trusted = configured ? origin === configured : process.env.NODE_ENV === "development" && ["http://127.0.0.1:3000", "http://localhost:3000"].includes(origin || "");
  if (request.method === "POST" && !trusted) return fail("Request origin not allowed.", 403);
  if (action === "login" && !request.headers.get("content-type")?.startsWith("application/json")) return fail("JSON required.", 415);
  if (Number(request.headers.get("content-length") || 0) > 4096) return fail("Request too large.", 413);
  try {
    let body: Uint8Array<ArrayBuffer> | undefined;
    if (action === "login" && request.body) {
      const reader = request.body.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        length += chunk.value.byteLength;
        if (length > 4096) { await reader.cancel(); return fail("Request too large.", 413); }
        chunks.push(chunk.value);
      }
      body = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
    }
    const token = request.cookies.get(companyAdminCookie)?.value;
    const upstream = await fetch(`${adminBackend}/auth/company/admin/${action}`, {
      method: request.method,
      headers: { "Content-Type": "application/json", ...(origin ? { Origin: origin } : {}), ...(token ? { Cookie: `${companyAdminCookie}=${encodeURIComponent(token)}` } : {}) },
      body, cache: "no-store", signal: AbortSignal.timeout(10000),
    });
    for (const name of ["set-cookie", "retry-after"]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(await upstream.text(), { status: upstream.status, headers });
  } catch {
    return fail("Unable to reach the company admin service. Please try again.", 503);
  }
}

export const GET = forward;
export const POST = forward;