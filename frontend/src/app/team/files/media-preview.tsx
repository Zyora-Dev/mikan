"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight, Download, ExternalLink, File, FileImage, Film, LoaderCircle, Maximize, Play, RotateCcw, X, ZoomIn, ZoomOut } from "lucide-react";
import drive from "./files.module.css";
import { History } from "lucide-react";
import VersionDialog from "./version-dialog";

export type PreviewFile = { id: string; name: string; size_bytes: number; state: string };

export function previewType(name: string) {
  const extension = name.split(".").pop()?.toLowerCase();
  if (["jpg", "jpeg", "png", "gif", "webp", "avif", "bmp"].includes(extension || "")) return "image";
  if (["mp4", "m4v", "webm", "mov"].includes(extension || "")) return "video";
  return extension === "pdf" ? "pdf" : null;
}

export function MediaThumbnail({ file }: { file: PreviewFile }) {
  const [failed, setFailed] = useState(false);
  const type = previewType(file.name);
  const source = `/team/files/${file.id}/view/media/${encodeURIComponent(file.name)}`;
  if (failed || (type === "image" && file.size_bytes > 20 * 1024 ** 2)) return type === "video" ? <Film size={44} strokeWidth={1.5} /> : <FileImage size={44} strokeWidth={1.5} />;
  return type === "image" ? <Image className={drive.thumbnail} src={source} alt="" width={320} height={180} unoptimized onError={() => setFailed(true)} /> : <>
    <video className={drive.thumbnail} src={`${source}#t=0.1`} preload="metadata" muted playsInline aria-hidden="true" onError={() => setFailed(true)} />
    <span className={drive.playBadge}><Play size={18} fill="currentColor" aria-hidden="true" /></span>
  </>;
}

export function FilePreviewPage({ file }: { file: PreviewFile }) {
  const router = useRouter();
  return <><title>{`${file.name} | Mikan`}</title><MediaPreview file={file} standalone onClose={() => router.replace("/team/files")} /></>;
}

export function SharedFilePage({ file, token }: { file: PreviewFile & { restricted_access?: boolean }; token: string }) {
  const router = useRouter();
  const [versions, setVersions] = useState(false);
  const [revision, setRevision] = useState(0);
  const downloadUrl = `/api/share/${token}/content`;
  const openVersions = file.restricted_access ? () => setVersions(true) : undefined;
  return <>{!previewType(file.name) || !file.size_bytes ? <main className={drive.sharedPage}><header><strong>Mikan Cloud</strong></header><section><File size={48} /><h1>{file.name}</h1><p>Preview unavailable</p><a href={downloadUrl} download><Download size={18} />Download file</a>{openVersions && <button type="button" className={drive.toolButton} onClick={openVersions}><History size={17} />Version history</button>}</section></main> :
    <MediaPreview key={revision} file={file} standalone sourceUrl={`/share/${token}/media/${encodeURIComponent(file.name)}`} downloadUrl={downloadUrl} onVersions={openVersions} onClose={() => router.replace("/")} />}
    {versions && <VersionDialog file={file} onClose={() => { setVersions(false); setRevision(value => value + 1); }} onChanged={() => router.refresh()} />}
  </>;
}

export default function MediaPreview({ file, previous, next, onNavigate, onClose, onVersions, standalone = false, sourceUrl, downloadUrl = `/api/team/files/${file.id}/content` }: { file: PreviewFile; previous?: PreviewFile; next?: PreviewFile; onNavigate?: (file: PreviewFile) => void; onClose: () => void; onVersions?: () => void; standalone?: boolean; sourceUrl?: string; downloadUrl?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [available, setAvailable] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [attempt, setAttempt] = useState(0);
  const type = previewType(file.name);
  const source = sourceUrl || `/team/files/${file.id}/view/media/${encodeURIComponent(file.name)}`;

  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function check() {
      try {
        const response = await fetch(source, { headers: { Range: "bytes=0-0" }, cache: "no-store", signal: controller.signal });
        await response.body?.cancel();
        if (!response.ok) throw new Error(response.status === 401 ? "Your session has expired. Sign in again to preview this file." : response.status === 404 ? "This file is no longer available." : "This file cannot be previewed right now.");
        if (!controller.signal.aborted) setAvailable(true);
      } catch (failure) { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Preview unavailable."); }
    }
    void check();
    return () => controller.abort();
  }, [source, attempt]);

  function retry() { setError(""); setLoaded(false); setAvailable(false); setAttempt(value => value + 1); }
  function failed() { setError(type === "video" ? "This video format or codec cannot be played in this browser." : "This file cannot be displayed in this browser."); }

  return <dialog ref={dialog} className={drive.previewDialog} data-standalone={standalone || undefined} aria-labelledby="media-preview-title" onClose={onClose}>
    <header className={drive.previewHeader}>
      <div><h2 id="media-preview-title" title={file.name}>{file.name}</h2><span>{type === "pdf" ? "PDF document" : type === "video" ? "Video" : "Image"}</span></div>
      {!standalone && <a href={`/team/files/${file.id}/view`} target="_blank" rel="noopener noreferrer" className={drive.previewButton} title="Open in new tab" aria-label="Open in new tab"><ExternalLink size={19} /></a>}
      {onVersions && <button type="button" className={drive.previewButton} title="Version history" aria-label="Version history" onClick={onVersions}><History size={19} /></button>}
      <a href={downloadUrl} download className={drive.previewButton} title="Download file" aria-label="Download file"><Download size={19} /></a>
      <button type="button" className={drive.previewButton} title="Close preview" aria-label="Close preview" onClick={() => dialog.current?.close()}><X size={21} /></button>
    </header>
    <div className={drive.previewStage}>
      {!error && !loaded && <div className={drive.previewLoading} role="status"><LoaderCircle size={25} /><span>Loading preview...</span></div>}
      {error ? <div className={drive.previewError} role="alert"><p>{error}</p><button type="button" onClick={retry}><RotateCcw size={16} />Retry</button><a href={downloadUrl} download><Download size={16} />Download file</a></div> : available && <>
        {type === "image" && <div className={drive.imageViewport}><div className={drive.imageCanvas} style={{ width: `${zoom * 100}%`, height: `${zoom * 100}%` }}><Image src={source} alt={file.name} width={1600} height={1000} unoptimized onLoad={() => setLoaded(true)} onError={failed} /></div></div>}
        {type === "video" && <video className={drive.previewVideo} src={source} controls playsInline preload="metadata" onLoadedMetadata={() => setLoaded(true)} onError={failed} />}
        {type === "pdf" && <iframe className={drive.previewPdf} src={source} title={`PDF preview: ${file.name}`} onLoad={() => setLoaded(true)} onError={failed} />}
      </>}
    </div>
    <footer className={drive.previewFooter}>
      <button type="button" className={drive.previewButton} disabled={!previous} title="Previous file on this page" aria-label="Previous file on this page" onClick={() => previous && onNavigate?.(previous)}><ChevronLeft size={20} /></button>
      {type === "image" && <div className={drive.zoomControls}>
        <button type="button" className={drive.previewButton} disabled={zoom <= 1 || !loaded} title="Zoom out" aria-label="Zoom out" onClick={() => setZoom(value => Math.max(1, value - 0.5))}><ZoomOut size={18} /></button>
        <output aria-label="Image zoom">{Math.round(zoom * 100)}%</output>
        <button type="button" className={drive.previewButton} disabled={zoom >= 4 || !loaded} title="Zoom in" aria-label="Zoom in" onClick={() => setZoom(value => Math.min(4, value + 0.5))}><ZoomIn size={18} /></button>
        <button type="button" className={drive.previewButton} disabled={zoom === 1} title="Fit image" aria-label="Fit image" onClick={() => setZoom(1)}><Maximize size={18} /></button>
      </div>}
      <button type="button" className={drive.previewButton} disabled={!next} title="Next file on this page" aria-label="Next file on this page" onClick={() => next && onNavigate?.(next)}><ChevronRight size={20} /></button>
    </footer>
  </dialog>;
}