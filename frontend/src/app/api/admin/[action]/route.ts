import { NextRequest } from "next/server";
import { adminBackend, adminCookie } from "@/lib/admin";

async function forward(request: NextRequest, context: { params: Promise<{ action: string }> }) {
  const { action } = await context.params;
  const allowed = request.method === "GET" ? action === "me" : ["login", "logout"].includes(action);
  if (!allowed) return Response.json({ detail: "Not found." }, { status: 404 });
  const origin = request.headers.get("origin");
  const configuredOrigin = process.env.ADMIN_PUBLIC_ORIGIN;
  const trustedOrigin = configuredOrigin
    ? origin === configuredOrigin
    : process.env.NODE_ENV === "development" && ["http://127.0.0.1:3000", "http://localhost:3000"].includes(origin || "");
  if (request.method === "POST" && !trustedOrigin) {
    return Response.json({ detail: "Request origin not allowed." }, { status: 403 });
  }
  if (action === "login" && !request.headers.get("content-type")?.startsWith("application/json")) {
    return Response.json({ detail: "JSON required." }, { status: 415 });
  }
  if (Number(request.headers.get("content-length") || 0) > 4096) {
    return Response.json({ detail: "Request too large." }, { status: 413 });
  }
  try {
    const body = action === "login" ? await request.text() : undefined;
    if (body && body.length > 4096) return Response.json({ detail: "Request too large." }, { status: 413 });
    const token = request.cookies.get(adminCookie)?.value;
    const upstream = await fetch(`${adminBackend}/auth/admin/${action}`, {
      method: request.method,
      headers: {
        "Content-Type": "application/json",
        ...(origin ? { Origin: origin } : {}),
        ...(token ? { Cookie: `${adminCookie}=${encodeURIComponent(token)}` } : {}),
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    const headers = new Headers({ "Content-Type": "application/json", "Cache-Control": "no-store" });
    for (const name of ["set-cookie", "retry-after"]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(await upstream.text(), { status: upstream.status, headers });
  } catch {
    return Response.json({ detail: "Unable to reach the admin service. Please try again." }, { status: 503 });
  }
}

export const GET = forward;
export const POST = forward;