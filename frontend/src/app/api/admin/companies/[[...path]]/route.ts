import { NextRequest } from "next/server";
import { adminBackend, adminCookie } from "@/lib/admin";

async function forward(request: NextRequest, context: { params: Promise<{ path?: string[] }> }) {
  const { path = [] } = await context.params;
  const identifier = path.length >= 1 && /^[1-9][0-9]*$/.test(path[0]);
  const allowed = request.method === "GET"
    ? path.length === 0 || (identifier && (path.length === 1 || (path.length === 2 && path[1] === "logo")))
    : request.method === "POST" ? path.length === 0 : ["PUT", "DELETE"].includes(request.method) && identifier && path.length === 1;
  if (!allowed) return Response.json({ detail: "Not found." }, { status: 404 });
  const token = request.cookies.get(adminCookie)?.value;
  if (!token) return Response.json({ detail: "Sign in required." }, { status: 401 });
  const origin = request.headers.get("origin");
  if (request.method !== "GET") {
    const configured = process.env.ADMIN_PUBLIC_ORIGIN;
    const trusted = configured ? origin === configured : process.env.NODE_ENV === "development" && ["http://127.0.0.1:3000", "http://localhost:3000"].includes(origin || "");
    if (!trusted) return Response.json({ detail: "Request origin not allowed." }, { status: 403 });
    if (!request.headers.get("content-type")?.startsWith("application/json")) return Response.json({ detail: "JSON required." }, { status: 415 });
  }
  const maximum = 3 * 1024 * 1024;
  if (Number(request.headers.get("content-length") || 0) > maximum) return Response.json({ detail: "Logo must be under 2 MB." }, { status: 413 });
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
          return Response.json({ detail: "Logo must be under 2 MB." }, { status: 413 });
        }
        chunks.push(result.value);
      }
      body = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
    }
    const upstream = await fetch(`${adminBackend}/companies/${path.join("/")}${request.nextUrl.search}`, {
      method: request.method,
      headers: { "Content-Type": "application/json", Cookie: `${adminCookie}=${encodeURIComponent(token)}`, ...(origin ? { Origin: origin } : {}) },
      body, cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    const headers = new Headers({ "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" });
    for (const name of ["content-type", "content-disposition", "content-security-policy"]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(await upstream.arrayBuffer(), { status: upstream.status, headers });
  } catch {
    return Response.json({ detail: "Unable to reach the company service. Please try again." }, { status: 503 });
  }
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const DELETE = forward;