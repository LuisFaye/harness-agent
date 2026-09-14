import { useState } from "react";
import { Eye, FileDown, FileText } from "lucide-react";

import type { Artifact } from "@/lib/api/schemas";

import {
  artifactDownloadHref,
  artifactPreviewKind,
  formatArtifactDate,
  formatArtifactSize,
  suffixLabel,
} from "./artifact-format";
import { ArtifactPreviewDialog } from "./artifact-preview-dialog";

interface ArtifactCardProps {
  artifact: Artifact;
}

export function ArtifactCard({ artifact }: ArtifactCardProps) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const previewKind = artifactPreviewKind(artifact.suffix);
  const canPreview = previewKind !== "none";
  const href = artifactDownloadHref(artifact.download_url);

  return (
    <li className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-lg border p-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <FileText aria-hidden="true" className="size-4 shrink-0 text-primary" />
          <strong className="min-w-0 truncate text-sm" title={artifact.filename}>
            {artifact.filename}
          </strong>
          <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-xs text-secondary-foreground">
            {suffixLabel(artifact.suffix)}
          </span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {formatArtifactSize(artifact.size)}
          </span>
          {artifact.retained ? (
            <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
              已保留
            </span>
          ) : null}
        </div>
        {artifact.summary ? (
          <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{artifact.summary}</p>
        ) : null}
        <p className="mt-1 text-xs text-muted-foreground">
          {formatArtifactDate(artifact.created_at)
            ? `创建于 ${formatArtifactDate(artifact.created_at)}`
            : null}
          {artifact.source_kind === "workspace_snapshot" && artifact.source_path
            ? ` · 来自 ${artifact.source_path}`
            : ""}
        </p>
        {canPreview ? (
          <ArtifactPreviewDialog
            artifact={artifact}
            onOpenChange={setPreviewOpen}
            open={previewOpen}
          />
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-1 self-center">
        {canPreview ? (
          <button
            aria-label={`预览 ${artifact.filename}`}
            className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-lg border text-sm font-medium text-primary hover:bg-accent"
            onClick={() => setPreviewOpen(true)}
            title={`预览 ${artifact.filename}`}
            type="button"
          >
            <Eye aria-hidden="true" className="size-4" />
            <span className="hidden sm:inline">预览</span>
          </button>
        ) : null}
        <a
          aria-label={`下载 ${artifact.filename}`}
          className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-lg border text-sm font-medium text-primary hover:bg-accent"
          href={href}
          rel="noopener noreferrer"
          target="_blank"
          title={`下载 ${artifact.filename}`}
        >
          <FileDown aria-hidden="true" className="size-4" />
          <span className="hidden sm:inline">下载</span>
        </a>
      </div>
    </li>
  );
}
