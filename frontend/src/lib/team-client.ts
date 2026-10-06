export class TeamRequestError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export const SINGLE_UPLOAD_BYTES = 5_000_000_000;
export const MAX_FILE_BYTES = 4 * 1024 ** 4;
const MULTIPART_THRESHOLD = 16 * 1024 ** 2;
const fingerprints = new WeakMap<File, Promise<string>>();

export function fileFingerprint(file: File): Promise<string> {
  const existing = fingerprints.get(file);
  if (existing) return existing;
  const result = (async () => {
    let digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`mikan-upload-v1:${file.size}`));
    for (let offset = 0; offset < file.size; offset += MULTIPART_THRESHOLD) {
      const chunk = await file.slice(offset, offset + MULTIPART_THRESHOLD).arrayBuffer();
      const chunkDigest = await crypto.subtle.digest("SHA-256", chunk);
      const combined = new Uint8Array(64);
      combined.set(new Uint8Array(digest));
      combined.set(new Uint8Array(chunkDigest), 32);
      digest = await crypto.subtle.digest("SHA-256", combined);
    }
    return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
  })();
  fingerprints.set(file, result);
  void result.catch(() => fingerprints.delete(file));
  return result;
}

export async function teamRequest<Result>(path: string, body?: object, signal?: AbortSignal, timeout = 25000, headers?: Record<string, string>): Promise<Result> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store",
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout),
  });
  const data = await response.json();
  if (!response.ok) throw new TeamRequestError(typeof data.detail === "string" ? data.detail : "Unable to complete the request.", response.status);
  return data;
}

export async function uploadTeamFile(identifier: string, file: File, progress: (value: number) => void) {
  if (file.size > MAX_FILE_BYTES) throw new Error("File exceeds 4 TiB.");
  const path = `/api/team/files/${identifier}`;
  const fingerprint = file.size > MULTIPART_THRESHOLD ? await fileFingerprint(file) : undefined;
  const identity = fingerprint ? { "X-Upload-Fingerprint": fingerprint } : undefined;
  async function transfer(target: string, body: Blob, confirmed: number) {
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await new Promise<void>((resolve, reject) => {
          const request = new XMLHttpRequest();
          request.open("POST", target);
          request.setRequestHeader("Content-Type", "application/octet-stream");
          if (fingerprint) request.setRequestHeader("X-Upload-Fingerprint", fingerprint);
          request.timeout = 300000;
          request.upload.onprogress = event => { if (event.lengthComputable) progress(Math.min(99, Math.floor((confirmed + event.loaded) * 100 / Math.max(file.size, 1)))); };
          request.onerror = request.ontimeout = () => reject(new TeamRequestError("Upload interrupted. Retry the same file.", 503));
          request.onabort = () => reject(new TeamRequestError("Upload interrupted. Retry the same file.", 499));
          request.onload = () => {
            let detail = "Upload could not be confirmed. Retry the same file.";
            try { const result = JSON.parse(request.responseText); if (typeof result.detail === "string") detail = result.detail; } catch {}
            if (request.status >= 200 && request.status < 300) resolve(); else reject(new TeamRequestError(detail, request.status));
          };
          request.send(body);
        });
        return;
      } catch (failure) {
        if (attempt === 2 || !(failure instanceof TeamRequestError) || (failure.status < 500 && failure.status !== 408 && failure.status !== 429)) throw failure;
        await new Promise(resolve => setTimeout(resolve, 500 * 2 ** attempt));
      }
    }
  }
  if (file.size <= MULTIPART_THRESHOLD) {
    await transfer(`${path}/upload`, file, 0);
  } else {
    const session = await teamRequest<{ state: string; part_bytes: number; uploaded_parts: number[] }>(`${path}/multipart`, {}, undefined, 300000, identity);
    if (session.state === "ready") { progress(100); return; }
    if (session.state !== "pending" || !Number.isSafeInteger(session.part_bytes) || session.part_bytes < MULTIPART_THRESHOLD || session.part_bytes > SINGLE_UPLOAD_BYTES || !Array.isArray(session.uploaded_parts)) throw new Error("Invalid upload session. Retry the same file.");
    const count = Math.ceil(file.size / session.part_bytes);
    if (count > 10000 || session.uploaded_parts.some(number => !Number.isInteger(number) || number < 1 || number > count)) throw new Error("Invalid upload parts. Retry the same file.");
    const uploaded = new Set(session.uploaded_parts);
    let confirmed = [...uploaded].reduce((total, number) => total + Math.min(session.part_bytes, file.size - (number - 1) * session.part_bytes), 0);
    progress(Math.min(99, Math.floor(confirmed * 100 / file.size)));
    for (let number = 1; number <= count; number++) {
      if (uploaded.has(number)) continue;
      const start = (number - 1) * session.part_bytes;
      const part = file.slice(start, Math.min(start + session.part_bytes, file.size));
      await transfer(`${path}/parts/${number}`, part, confirmed);
      confirmed += part.size;
      progress(Math.min(99, Math.floor(confirmed * 100 / file.size)));
    }
    await teamRequest(`${path}/multipart/complete`, {}, undefined, 300000, identity);
  }
  progress(100);
}