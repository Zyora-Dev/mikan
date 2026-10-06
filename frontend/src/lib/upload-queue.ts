import { MAX_FILE_BYTES, teamRequest, uploadTeamFile } from "./team-client";

export type UploadItem = {
  key: string; id?: string; file: File | null; name: string; size: number;
  folder: string; modified: number; progress: number;
  status: "queued" | "uploading" | "complete" | "failed"; error: string;
};

export class UploadQueue {
  private items: UploadItem[] = [];
  private listeners = new Set<() => void>();
  private running = false;
  private cancelling = new Set<string>();
  private revision = 0;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = () => this.items;
  version = () => this.revision;
  private publish() { this.items = [...this.items]; this.listeners.forEach(listener => listener()); }
  private update(key: string, update: Partial<UploadItem>) {
    this.items = this.items.map(item => item.key === key ? { ...item, ...update } : item);
    this.publish();
  }
  enqueue(files: File[], folder: string, limit: number, pending?: { id: string; name: string; size_bytes: number }) {
    if (pending && this.cancelling.has(pending.id)) throw new Error("Upload cancellation is in progress.");
    if (!files.length) throw new Error("Select at least one file.");
    if (pending && (files.length !== 1 || files[0].name !== pending.name || files[0].size !== pending.size_bytes)) throw new Error("Select the original file with the same name and size.");
    const oversized = files.find(file => file.size > Math.min(limit, MAX_FILE_BYTES));
    if (oversized) throw new Error(`${oversized.name} exceeds the maximum single file size allowed by your company.`);
    for (const file of files) {
      const existing = this.items.find(item => pending ? item.id === pending.id : item.status !== "complete" && item.folder === folder && item.name === file.name && item.size === file.size && item.modified === file.lastModified);
      if (existing) {
        if (existing.status === "failed") this.update(existing.key, { file, status: "queued", error: "", progress: 0 });
        continue;
      }
      this.items.push({ key: crypto.randomUUID(), id: pending?.id, file, name: file.name, size: file.size, folder, modified: file.lastModified, progress: 0, status: "queued", error: "" });
    }
    this.publish();
    void this.process();
  }
  retry(key: string) {
    const item = this.items.find(item => item.key === key);
    if (!item || item.status !== "failed" || !item.file) return;
    if (item.id && this.cancelling.has(item.id)) return;
    this.update(key, { status: "queued", progress: 0, error: "" });
    void this.process();
  }
  async cancel(identifier: string) {
    if (this.cancelling.has(identifier)) throw new Error("Upload cancellation is in progress.");
    if (this.items.some(item => item.id === identifier && (item.status === "queued" || item.status === "uploading"))) throw new Error("Wait for the active upload attempt to finish before cancelling.");
    this.cancelling.add(identifier);
    try {
      await teamRequest(`/api/team/files/${identifier}/cancel`, {}, undefined, 300000);
      this.items = this.items.filter(item => item.id !== identifier);
      this.revision += 1; this.publish();
    } finally { this.cancelling.delete(identifier); }
  }
  dismiss() {
    if (this.items.some(item => item.status === "queued" || item.status === "uploading")) return;
    this.items = []; this.publish();
  }
  private async process() {
    if (this.running) return;
    this.running = true;
    try {
      let item: UploadItem | undefined;
      while ((item = this.items.find(entry => entry.status === "queued"))) {
        const current = item;
        this.update(current.key, { status: "uploading" });
        try {
          let identifier = current.id;
          if (!identifier) {
            const result = await teamRequest<{ id: string }>("/api/team/files/uploads", { name: current.name, folder: current.folder, size_bytes: current.size, upload_key: current.key });
            identifier = result.id;
            this.update(current.key, { id: identifier });
          }
          await uploadTeamFile(identifier, current.file!, progress => this.update(current.key, { progress }));
          this.update(current.key, { status: "complete", progress: 100, file: null });
        } catch (failure) {
          this.update(current.key, { status: "failed", error: failure instanceof Error ? failure.message : "Unable to upload file." });
        }
        this.revision += 1; this.publish();
      }
    } finally { this.running = false; }
  }
}

const queues = new Map<string, UploadQueue>();
export function getUploadQueue(owner: string) {
  let queue = queues.get(owner);
  if (!queue) { queue = new UploadQueue(); queues.set(owner, queue); }
  return queue;
}