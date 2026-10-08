import "server-only";
import { createHmac } from "node:crypto";
import { isIP } from "node:net";
import type { NextRequest } from "next/server";
import { adminBackend } from "./admin";
import { SINGLE_UPLOAD_BYTES } from "./team-client";

export async function teamProxy(request: NextRequest, path: string, scope: "company" | "team" | "share") {
  const headers = new Headers({ "Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  const sharedRead = scope === "share" && /^[0-9a-f]{64}(?:\/(?:content|preview))?$/.test(path);
  const uuid = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
  const versionContent = scope === "team" && new RegExp(`^files/${uuid}/versions/${uuid}/content$`, "i").test(path);
  const fileRead = versionContent || (sharedRead && path.includes("/")) || (scope === "team" && /^files\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/(?:content|preview)$/i.test(path)) || (scope === "company" && /^data\/files\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/content$/i.test(path));
  const rootFileRead = scope === "company" && new RegExp(`^data/root/files/${uuid}/content$`, "i").test(path);
  const filePreview = fileRead && scope !== "company" && path.endsWith("/preview");
  if (scope === "share" || (scope === "team" && (path === "files" || path.startsWith("files/"))) || (scope === "company" && path.startsWith("data/"))) {
    headers.set("Cache-Control", "private, no-store, max-age=0");
    headers.set("CDN-Cache-Control", "no-store");
    headers.set("Cloudflare-CDN-Cache-Control", "no-store");
    headers.set("Vary", "Cookie");
    headers.set("Content-Security-Policy", "sandbox; default-src 'none'; frame-ancestors 'none'");
    headers.set("Referrer-Policy", "no-referrer");
    headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");
  }
  const fail = (detail: string, status: number) => Response.json({ detail }, { status, headers });
  const sharingRead = scope === "team" && (["files/shared", "files/trash"].includes(path) || new RegExp(`^files/${uuid}/(?:shares|share-members|link|versions|activity)$`, "i").test(path));
  const sharingWrite = scope === "team" && new RegExp(`^files/${uuid}/(?:trash|restore|link|versions(?:/${uuid}/restore)?|shares(?:/[1-9][0-9]{0,18}/(?:remove|retry-email|permission))?)$`, "i").test(path);
  const profileWrite = scope === "team" && path === "profile" && request.method === "POST";
  const profilePhoto = scope === "team" && path === "profile/photo" && request.method === "GET";
  const fileUpload = scope === "team" && new RegExp(`^files/${uuid}/upload$`, "i").test(path);
  const fileCancel = scope === "team" && new RegExp(`^files/${uuid}/cancel$`, "i").test(path);
  const filePart = scope === "team" && new RegExp(`^files/${uuid}/parts/(?:[1-9][0-9]{0,3}|10000)$`, "i").test(path);
  const fileMultipart = scope === "team" && new RegExp(`^files/${uuid}/multipart(?:/complete)?$`, "i").test(path);
  const workflowRead = /^workflows(?:\/(?:options|runs|jobs|uploads|[1-9][0-9]*))?$/.test(path) || new RegExp(`^workflows/runs/${uuid}$`, "i").test(path);
  const workflowWrite = /^workflows(?:\/(?:validate|uploads|[1-9][0-9]*))?$/.test(path) || new RegExp(`^workflows/runs/${uuid}/cancel$`, "i").test(path) || new RegExp(`^workflows/jobs/${uuid}/retry$`, "i").test(path);
  const companyRead = path === "" || path === "people" || path === "folders" || /^folders\/[1-9][0-9]*\/activity$/.test(path) || workflowRead || /^data\/(files|folders|activity|trash-settings|root)$/.test(path) || fileRead || rootFileRead;
  const companyWrite = path === "" || path === "invite" || /^people\/[1-9][0-9]*\/(edit|resend|disable|delete)$/.test(path) || /^[1-9][0-9]*\/delete$/.test(path) || /^folders\/[1-9][0-9]*$/.test(path) || workflowWrite || ["data/folders", "data/trash-settings", "data/root/folders"].includes(path) || new RegExp(`^data/files/${uuid}$`, "i").test(path);
  const teamRead = ["auth/me", "people", "dashboard", "storage", "files", "workflows", "workflows/files", "workflows/people", "workflows/runs", "workflows/notifications"].includes(path) || /^workflows\/[1-9][0-9]*$/.test(path) || new RegExp(`^workflows/runs/${uuid}$`, "i").test(path) || fileRead || sharingRead;
  const teamWrite = ["files/folders", "files/uploads", "auth/password", "auth/otp/request", "auth/otp/verify", "auth/recover", "auth/verification", "auth/activate", "auth/logout"].includes(path) || /^workflows\/[1-9][0-9]*\/submit$/.test(path) || /^workflows\/notifications\/[1-9][0-9]*\/read$/.test(path) || new RegExp(`^workflows/runs/${uuid}/(?:decide|cancel)$`, "i").test(path) || fileUpload || filePart || fileMultipart || sharingWrite;
  if (!["GET", "POST"].includes(request.method)) return fail("Not found.", 404);
  if (!(scope === "share" ? request.method === "GET" && sharedRead : scope === "company" ? (request.method === "GET" ? companyRead : companyWrite) : (request.method === "GET" ? teamRead || profilePhoto : teamWrite || fileCancel || profileWrite))) return fail("Not found.", 404);
  const cookieName = scope === "company" ? "mikan_company_admin_session" : "mikan_team_session";
  const cookie = request.cookies.get(cookieName)?.value;
  const token = scope === "share" && !/^[A-Za-z0-9_-]{32,128}$/.test(cookie || "") ? undefined : cookie;
  if ((sharingRead || sharingWrite) && (!token || !/^[A-Za-z0-9_-]{32,128}$/.test(token))) return fail("Sign in required.", 401);
  if (scope === "company" && !token) return fail("Sign in required.", 401);
  if (scope === "team" && ["storage", "dashboard", "people"].includes(path)) {
    headers.set("Cache-Control", "private, no-store, max-age=0");
    headers.set("Vary", "Cookie");
    if (!token || !/^[A-Za-z0-9_-]{32,128}$/.test(token)) return fail("Sign in required.", 401);
  }
  if ((profileWrite || profilePhoto) && (!token || !/^[A-Za-z0-9_-]{32,128}$/.test(token))) return fail("Sign in required.", 401);
  if (profilePhoto) {
    headers.set("Cache-Control", "private, no-store, max-age=0");
    headers.set("CDN-Cache-Control", "no-store");
    headers.set("Vary", "Cookie");
    headers.set("Referrer-Policy", "no-referrer");
    try {
      const upstream = await fetch(`${adminBackend}/team/profile/photo`, {
        cache: "no-store", redirect: "error", signal: AbortSignal.any([request.signal, AbortSignal.timeout(20000)]),
        headers: { Cookie: `${cookieName}=${encodeURIComponent(token!)}`, "Accept-Encoding": "identity" },
      });
      if (!upstream.ok || upstream.headers.get("Content-Type") !== "image/webp" || upstream.headers.get("Content-Encoding")) {
        await upstream.body?.cancel();
        return fail(upstream.status === 401 ? "Sign in required." : "Profile photo unavailable.", [401, 404].includes(upstream.status) ? upstream.status : 503);
      }
      headers.set("Content-Type", "image/webp");
      headers.set("Content-Disposition", 'inline; filename="profile.webp"');
      return new Response(upstream.body, { headers });
    } catch { return fail("Profile photo unavailable.", 503); }
  }
  if (fileRead || rootFileRead) {
    if (scope !== "share" && (!token || !/^[A-Za-z0-9_-]{32,128}$/.test(token))) return fail("Sign in required.", 401);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const upstream = await fetch(`${adminBackend}/${scope === "share" ? "team/files/link" : scope === "company" ? "company/teams" : "team"}/${path}`, {
        cache: "no-store", redirect: "error",
        signal: AbortSignal.any([request.signal, controller.signal]),
        headers: { ...(token ? { Cookie: `${cookieName}=${token}` } : {}), "Accept-Encoding": "identity", ...(filePreview && request.headers.get("range") ? { Range: request.headers.get("range")! } : {}) },
      });
      clearTimeout(timeout);
      if ((upstream.status !== 200 && !(filePreview && upstream.status === 206)) || !upstream.body) {
        await upstream.body?.cancel();
        if (upstream.status === 401) return fail("Sign in required.", 401);
        if (upstream.status === 403) return fail("You do not have access to this file.", 403);
        if (upstream.status === 404) return fail("File not found.", 404);
        if (filePreview && upstream.status === 415) return fail("Preview is not available for this file type.", 415);
        if (filePreview && upstream.status === 416) {
          const range = upstream.headers.get("Content-Range");
          if (range && /^bytes \*\/[0-9]+$/.test(range)) headers.set("Content-Range", range);
          return fail("Requested range is not available.", 416);
        }
        return fail("File temporarily unavailable.", 503);
      }
      headers.set("Content-Type", "application/octet-stream");
      const disposition = upstream.headers.get("Content-Disposition");
      headers.set("Content-Disposition", disposition?.startsWith("attachment;") ? disposition : 'attachment; filename="download"');
      if (filePreview) {
        const type = upstream.headers.get("Content-Type") || "";
        if (!/^(?:image\/(?:jpeg|png|gif|webp|avif|bmp)|video\/(?:mp4|webm|quicktime)|application\/pdf)$/.test(type) || upstream.headers.get("Content-Encoding")) {
          await upstream.body.cancel();
          return fail("Preview is not available for this file type.", 415);
        }
        headers.set("Content-Type", type);
        headers.set("Content-Disposition", disposition?.startsWith("inline;") ? disposition : 'inline; filename="preview"');
        headers.set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'self'");
        headers.set("Accept-Ranges", "bytes");
        for (const key of ["Content-Length", "Content-Range"]) {
          const value = upstream.headers.get(key);
          if (value) headers.set(key, value);
        }
      }
      return new Response(upstream.body, { status: upstream.status, headers });
    } catch { return fail("File temporarily unavailable.", 503); }
    finally { clearTimeout(timeout); }
  }
  const origin = request.headers.get("origin");
  const configured = process.env.ADMIN_PUBLIC_ORIGIN;
  const trusted = configured ? origin === configured : process.env.NODE_ENV === "development" && ["http://127.0.0.1:3000", "http://localhost:3000"].includes(origin || "");
  if (request.method !== "GET" && !trusted) return fail("Request origin not allowed.", 403);
  if ((fileMultipart || fileCancel) && (!token || !/^[A-Za-z0-9_-]{32,128}$/.test(token))) return fail("Sign in required.", 401);
  const fingerprint = request.headers.get("x-upload-fingerprint");
  if ((fileMultipart || filePart) && (!fingerprint || !/^[a-f0-9]{64}$/.test(fingerprint))) return fail("Upload content identity required.", 422);
  const identity: Record<string, string> = fingerprint && (fileMultipart || filePart) ? { "X-Upload-Fingerprint": fingerprint } : {};
  if (fileUpload || filePart) {
    if (!token || !/^[A-Za-z0-9_-]{32,128}$/.test(token)) return fail("Sign in required.", 401);
    if (request.headers.get("content-type") !== "application/octet-stream") return fail("Binary file content required.", 415);
    if (Number(request.headers.get("content-length") || 0) > SINGLE_UPLOAD_BYTES) return fail("Upload request exceeds 5 GB.", 413);
    let length = 0;
    let oversized = false;
    const body = request.body?.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({ transform(chunk, controller) {
      length += chunk.byteLength;
      if (length > SINGLE_UPLOAD_BYTES) { oversized = true; throw new Error("Upload too large"); }
      controller.enqueue(chunk);
    } }));
    try {
      const init: RequestInit & { duplex: "half" } = { method: "POST", body, duplex: "half", cache: "no-store", redirect: "error",
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(300000)]),
        headers: { "Content-Type": "application/octet-stream", Origin: origin!, Cookie: `${cookieName}=${token}`, ...identity } };
      const upstream = await fetch(`${adminBackend}/team/${path}`, init);
      return new Response(await upstream.text(), { status: upstream.status, headers });
    } catch { return fail(oversized ? "Upload request exceeds 5 GB." : "Upload interrupted. Retry the same file.", oversized ? 413 : 503); }
  }
  if (request.method !== "GET" && !request.headers.get("content-type")?.startsWith("application/json")) return fail("JSON required.", 415);
  const bodyLimit = profileWrite ? 2800000 : path === "workflows" || path.startsWith("workflows/") ? 65536 : 16384;
  if (Number(request.headers.get("content-length") || 0) > bodyLimit) return fail("Request too large.", 413);
  try {
    let body: Uint8Array<ArrayBuffer> | undefined;
    if (request.method !== "GET" && request.body) {
      const reader = request.body.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        length += chunk.value.byteLength;
        if (length > bodyLimit) { await reader.cancel(); return fail("Request too large.", 413); }
        chunks.push(chunk.value);
      }
      body = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
    }
    const target = scope === "share" ? `/team/files/link/${path}` : scope === "company" ? `/company/teams/${path}` : path.startsWith("auth/") ? `/auth/team/${path.slice(5)}` : `/team/${path}`;
    const clientIdentity: Record<string, string> = {};
    if (scope === "team" && ["auth/password", "auth/otp/request", "auth/otp/verify", "auth/recover"].includes(path)) {
      const secret = process.env.TEAM_PROXY_SECRET;
      const header = process.env.TEAM_CLIENT_IP_HEADER?.trim();
      if (header) {
        if (!secret || secret.length < 32 || !header || !/^[a-z0-9-]+$/.test(header)) return fail("Authentication proxy is not configured.", 503);
        const address = request.headers.get(header) || "";
        if (!isIP(address)) return fail("Verified client address required.", 403);
        const timestamp = String(Math.floor(Date.now() / 1000));
        clientIdentity["X-Mikan-Client-IP"] = address;
        clientIdentity["X-Mikan-Client-Time"] = timestamp;
        clientIdentity["X-Mikan-Client-Signature"] = createHmac("sha256", secret).update(`${timestamp}\n${request.method}\n${target}\n${address}`).digest("hex");
      }
    }
    const params = new URLSearchParams();
    for (const key of ["page", "page_size", "search", "from_date", "to_date", "team_id", ...(scope === "team" && path === "files" ? ["folder", "owner_id"] : []), ...(path.startsWith("workflows/") ? ["workflow_id", "node_id", "view"] : []), ...(scope === "company" && path.startsWith("data/") ? ["owner_id", "folder", "view"] : [])]) {
      const value = request.nextUrl.searchParams.get(key);
      if (value !== null) params.set(key, value);
    }
    const upstream = await fetch(`${adminBackend}${target}${params.size ? `?${params}` : ""}`, {
      method: request.method, body, cache: "no-store", redirect: "error", signal: AbortSignal.any([request.signal, AbortSignal.timeout(fileMultipart || fileCancel ? 300000 : 20000)]),
      headers: { "Content-Type": "application/json", ...(origin ? { Origin: origin } : {}), ...(token ? { Cookie: `${cookieName}=${encodeURIComponent(token)}` } : {}), ...identity, ...clientIdentity },
    });
    for (const key of scope === "share" ? ["retry-after"] : ["set-cookie", "retry-after"]) { const value = upstream.headers.get(key); if (value) headers.set(key, value); }
    return new Response(await upstream.text(), { status: upstream.status, headers });
  } catch { return fail("The team service is temporarily unavailable. Please try again.", 503); }
}