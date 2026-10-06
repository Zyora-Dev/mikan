import { NextRequest } from "next/server";
import { adminBackend, adminCookie } from "@/lib/admin";

async function forward(request: NextRequest) {
  const headers = { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" };
  const fail = (detail: string, status: number) => Response.json({ detail }, { status, headers });
  const token = request.cookies.get(adminCookie)?.value;
  if (!token) return fail("Sign in required.", 401);
  const origin = request.headers.get("origin");
  if (request.method === "POST") {
    const configured = process.env.ADMIN_PUBLIC_ORIGIN;
    const trusted = configured ? origin === configured : process.env.NODE_ENV === "development" && ["http://127.0.0.1:3000", "http://localhost:3000"].includes(origin || "");
    if (!trusted) return fail("Request origin not allowed.", 403);
    if (!request.headers.get("content-type")?.startsWith("application/json")) return fail("JSON required.", 415);
  }
  const maximum = 8192;
  if (Number(request.headers.get("content-length") || 0) > maximum) return fail("Request is too large.", 413);
  try {
    let body: Uint8Array<ArrayBuffer> | undefined;
    if (request.method === "POST" && request.body) {
      const reader = request.body.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      while (true) {
        const result = await reader.read();
        if (result.done) break;
        length += result.value.byteLength;
        if (length > maximum) {
          await reader.cancel();
          return fail("Request is too large.", 413);
        }
        chunks.push(result.value);
      }
      body = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
    }
    const upstream = await fetch(`${adminBackend}/company-admins/${request.nextUrl.search}`, {
      method: request.method,
      headers: { "Content-Type": "application/json", Cookie: `${adminCookie}=${encodeURIComponent(token)}`, ...(origin ? { Origin: origin } : {}) },
      body, cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    return new Response(await upstream.text(), { status: upstream.status, headers: { ...headers, "Content-Type": "application/json" } });
  } catch {
    return fail("Unable to reach the admin service. Please try again.", 503);
  }
}

export const GET = forward;
export const POST = forward;