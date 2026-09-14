import { apiUrl } from "@/lib/api/client";

export type ArtifactPreviewKind = "markdown" | "text" | "image" | "pdf" | "docx" | "html" | "none";

export function artifactPreviewKind(suffix: string): ArtifactPreviewKind {
  switch (suffix.toLowerCase()) {
    case ".md":
      return "markdown";
    case ".html":
    case ".htm":
    case ".svg":
      return "html";
    case ".txt":
    case ".csv":
    case ".json":
      return "text";
    case ".png":
    case ".jpg":
    case ".jpeg":
    case ".webp":
      return "image";
    case ".pdf":
      return "pdf";
    case ".docx":
      return "docx";
    default:
      return "none";
  }
}

export function artifactDownloadHref(downloadUrl: string): string {
  return apiUrl(downloadUrl);
}

export function formatArtifactSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatArtifactDate(value: string | null): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function suffixLabel(suffix: string): string {
  const normalized = suffix.replace(/^\./, "").toUpperCase();
  return normalized || "FILE";
}
