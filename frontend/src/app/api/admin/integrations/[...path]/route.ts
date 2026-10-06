import { NextRequest } from "next/server";
import { adminBackend, adminCookie } from "@/lib/admin";

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const whatsappAllowed = path[0] === "whatsapp" && (
    path.length === 1 ? ["GET", "PUT"].includes(request.method) :
    path.length === 2 ? (path[1] === "consents" && ["GET", "POST"].includes(request.method)) || (path[1] === "send" && request.method === "POST") :
    path.length === 4 && path[1] === "consents" && /^[1-9]\d{0,17}$/.test(path[2]) && path[3] === "revoke" && request.method === "POST"
  );
  const mailAllowed = path[0] === "zeptomail" && (path.length === 1
    ? ["GET", "PUT", "DELETE"].includes(request.method)
    : path.length === 2 && path[1] === "test" && request.method === "POST");
  const storageAllowed = path[0] === "storage" && (
    path.length === 1 ? request.method === "GET" :
    ["r2", "s3"].includes(path[1]) && (
      path.length === 2 ? ["GET", "POST"].includes(request.method) :
      /^[1-9]\d{0,17}$/.test(path[2]) && (
        path.length === 3 ? ["PUT", "DELETE"].includes(request.method) :
        path.length === 4 && path[3] === "test" && request.method === "POST"
      )
    )
  );
  const headers = { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" };
  const fail = (detail: string, status: number) => Response.json({ detail }, { status, headers });
  if (!mailAllowed && !storageAllowed && !whatsappAllowed) return fail("Not found.", 404);
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
    const search = new URLSearchParams();
    if ((storageAllowed || (whatsappAllowed && path[1] === "consents")) && path.length === 2 && request.method === "GET") {
      for (const key of ["search", "page", "from_date", "to_date", ...(whatsappAllowed ? ["active"] : [])]) {
        const value = request.nextUrl.searchParams.get(key);
        if (value) {
          if (value.length > 160) return fail("Filter is too long.", 422);
          search.set(key, value);
        }
      }
    }
    const upstream = await fetch(`${adminBackend}/integrations/${path.join("/")}${search.size ? `?${search}` : ""}`, {
      method: request.method,
      headers: { "Content-Type": "application/json", Cookie: `${adminCookie}=${encodeURIComponent(token)}`, ...(origin ? { Origin: origin } : {}) },
      body, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(storageAllowed && path[3] === "test" ? 60000 : 15000),
    });
    return new Response(await upstream.text(), { status: upstream.status, headers: { ...headers, "Content-Type": "application/json" } });
  } catch {
    if (whatsappAllowed && path[1] === "send") return fail("Send outcome is unknown. Check WhatsApp before sending again to avoid a duplicate.", 503);
    return fail("Unable to reach the integration service. Please try again.", 503);
  }
}

export const GET = forward;
export const PUT = forward;
export const POST = forward;
export const DELETE = forward;