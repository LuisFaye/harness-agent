import { useState } from "react";
import { ArrowDownToLine, Eye, FileCode2, FileImage, FileSpreadsheet, FileText } from "lucide-react";

import type { Artifact } from "@/lib/api/schemas";
import { artifactDownloadHref, artifactPreviewKind, formatArtifactDate, formatArtifactSize, suffixLabel } from "./artifact-format";
import { ArtifactPreviewDialog } from "./artifact-preview-dialog";

export function ArtifactCard({ artifact }: { artifact: Artifact }) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const previewKind = artifactPreviewKind(artifact.suffix);
  const canPreview = previewKind !== "none";
  const suffix = artifact.suffix.toLowerCase().replace(/^\./, "");
  const Icon = ["png", "jpg", "jpeg", "webp", "gif", "svg"].includes(suffix) ? FileImage
    : ["csv", "xlsx", "xls"].includes(suffix) ? FileSpreadsheet
    : ["json", "html", "js", "ts", "py", "sql", "sh", "css"].includes(suffix) ? FileCode2 : FileText;
  return (
    <li className="artifact-tile min-w-0 rounded-2xl bg-card p-4">
      <div className="flex min-w-0 items-center gap-3">
        <span className="grid size-11 shrink-0 place-items-center rounded-xl border border-primary/15 bg-primary-soft text-primary shadow-2xs">
          <Icon aria-hidden="true" className="size-5.5" />
        </span>
        <div className="min-w-0 flex-1">
          {canPreview ? (
            <button className="block w-full truncate text-left text-sm font-semibold text-foreground transition-colors hover:text-primary" onClick={() => setPreviewOpen(true)} title={artifact.filename} type="button" aria-label={`预览 ${artifact.filename}`}>
              {artifact.filename}
            </button>
          ) : <strong className="block truncate text-sm font-semibold text-foreground" title={artifact.filename}>{artifact.filename}</strong>}
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span className="rounded-md bg-muted px-1.5 py-0.5 font-medium text-secondary-foreground">{suffixLabel(artifact.suffix)}</span>
            <span aria-hidden="true">·</span>
            <span className="tabular-nums">{formatArtifactSize(artifact.size)}</span>
            {artifact.retained ? <span className="font-medium text-primary">已保留</span> : null}
          </p>
        </div>
        <a aria-label={`下载 ${artifact.filename}`} className="artifact-action" href={artifactDownloadHref(artifact.download_url)} rel="noopener noreferrer" target="_blank" title={`下载 ${artifact.filename}`}>
          <ArrowDownToLine aria-hidden="true" className="size-[18px]" />
        </a>
      </div>
      {artifact.summary ? <p className="mt-3 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{artifact.summary}</p> : null}
      <div className="mt-3 flex min-w-0 items-center justify-between gap-2 border-t border-border/60 pt-2.5 text-xs text-muted-foreground">
        <span className="min-w-0 truncate" title={artifact.source_path || undefined}>
          {artifact.source_kind === "workspace_snapshot" && artifact.source_path
            ? artifact.source_path : formatArtifactDate(artifact.created_at)}
        </span>
        {canPreview ? <button className="inline-flex min-h-7 shrink-0 items-center gap-1.5 rounded-lg border border-border/70 bg-muted/40 px-2.5 py-0.5 font-medium text-foreground/85 transition-colors hover:border-primary/25 hover:bg-primary-soft hover:text-primary" onClick={() => setPreviewOpen(true)} type="button">
          <Eye aria-hidden="true" className="size-3.5" />预览
        </button> : null}
      </div>
      {canPreview ? <ArtifactPreviewDialog artifact={artifact} onOpenChange={setPreviewOpen} open={previewOpen} /> : null}
    </li>
  );
}
