import { NextRequest } from "next/server";
import { adminBackend } from "@/lib/admin";
import { companyAdminCookie } from "@/lib/company-admin";

async function forward(request: NextRequest, context: { params: Promise<{ path?: string[] }> }) {
  const { path = [] } = await context.params;
  const isLogo = path.length === 1 && path[0] === "logo";
  if (!(path.length === 0 || (request.method === "GET" && isLogo))) return Response.json({ detail: "Not found." }, { status: 404 });
  const headers = new Headers({ "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" });
  const token = request.cookies.get(companyAdminCookie)?.value;
  if (!token) return Response.json({ detail: "Sign in required." }, { status: 401, headers });
  const origin = request.headers.get("origin");
  if (request.method === "PUT") {
    const configured = process.env.ADMIN_PUBLIC_ORIGIN;
    const trusted = configured ? origin === configured : process.env.NODE_ENV === "development" && ["http://127.0.0.1:3000", "http://localhost:3000"].includes(origin || "");
    if (!trusted) return Response.json({ detail: "Request origin not allowed." }, { status: 403, headers });
    if (!request.headers.get("content-type")?.startsWith("application/json")) return Response.json({ detail: "JSON required." }, { status: 415, headers });
  }
  const maximum = 3 * 1024 * 1024;
  if (Number(request.headers.get("content-length") || 0) > maximum) return Response.json({ detail: "Logo must be under 2 MB." }, { status: 413, headers });
  try {
    let body: Uint8Array<ArrayBuffer> | undefined;
    if (request.method === "PUT" && request.body) {
      const reader = request.body.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      while (true) {
        const result = await reader.read();
        if (result.done) break;
        length += result.value.byteLength;
        if (length > maximum) {
          await reader.cancel();
          return Response.json({ detail: "Logo must be under 2 MB." }, { status: 413, headers });
        }
        chunks.push(result.value);
      }
      body = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
    }
    const upstream = await fetch(`${adminBackend}/company/profile${isLogo ? "/logo" : ""}`, {
      method: request.method, body, cache: "no-store", signal: AbortSignal.timeout(15000),
      headers: { "Content-Type": "application/json", Cookie: `${companyAdminCookie}=${encodeURIComponent(token)}`, ...(origin ? { Origin: origin } : {}) },
    });
    for (const name of ["content-type", "content-disposition", "content-security-policy"]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(await upstream.arrayBuffer(), { status: upstream.status, headers });
  } catch {
    return Response.json({ detail: "Unable to reach the company service. Please try again." }, { status: 503, headers });
  }
}

export const GET = forward;
export const PUT = forward;