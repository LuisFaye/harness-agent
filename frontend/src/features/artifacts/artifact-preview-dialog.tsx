import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { FileDown } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Artifact } from "@/lib/api/schemas";

import {
  artifactDownloadHref,
  artifactPreviewKind,
  type ArtifactPreviewKind,
} from "./artifact-format";

const MessageResponse = lazy(async () => ({
  default: (await import("@/components/ai-elements/message-response")).MessageResponse,
}));

interface ArtifactPreviewDialogProps {
  artifact: Artifact;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ArtifactPreviewDialog({
  artifact,
  open,
  onOpenChange,
}: ArtifactPreviewDialogProps) {
  const kind = artifactPreviewKind(artifact.suffix);
  const href = artifactDownloadHref(artifact.download_url);

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="flex max-h-[calc(100svh-2rem)] flex-col overflow-hidden sm:max-w-4xl">
        <DialogHeader className="pr-8">
          <DialogTitle className="truncate" title={artifact.filename}>
            {artifact.filename}
          </DialogTitle>
          <DialogDescription>
            {kind === "docx"
              ? "预览仅供查看版式，下载后用 Word 打开即为最终文件。"
              : kind === "html"
                ? "支持 HTML/SVG 样式和动画预览；脚本交互请下载后打开。"
                : "在浏览器中预览当前产物。"}
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 min-w-0 flex-1 overflow-auto">
          {open ? <ArtifactPreviewBody href={href} kind={kind} /> : null}
        </div>
        <DialogFooter>
          <a
            className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-border bg-background px-3 text-sm font-medium hover:bg-muted"
            href={href}
            rel="noopener noreferrer"
            target="_blank"
          >
            <FileDown aria-hidden="true" className="size-4" />
            下载
          </a>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ArtifactPreviewBody({
  href,
  kind,
}: {
  href: string;
  kind: ArtifactPreviewKind;
}) {
  if (kind === "image") {
    return (
      <img
        alt="产物预览"
        className="mx-auto max-h-[min(70vh,720px)] max-w-full rounded-lg object-contain"
        src={href}
      />
    );
  }
  if (kind === "pdf") {
    return (
      <iframe
        className="h-[min(70vh,720px)] w-full rounded-lg border bg-white"
        src={href}
        title="PDF 预览"
      />
    );
  }
  if (kind === "docx") {
    return <DocxPreview href={href} />;
  }
  if (kind === "markdown" || kind === "text" || kind === "html") {
    return <TextPreview href={href} markdown={kind === "markdown"} html={kind === "html"} />;
  }
  return (
    <p className="text-sm text-muted-foreground" role="status">
      暂不支持在浏览器中预览此格式，请下载后打开。
    </p>
  );
}

function TextPreview({ href, markdown, html }: { href: string; markdown: boolean; html: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setText(null);
    setError(null);
    void fetch(href, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("预览加载失败");
        }
        return response.text();
      })
      .then(setText)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") {
          return;
        }
        setError(cause instanceof Error ? cause.message : "预览加载失败");
      });
    return () => controller.abort();
  }, [href]);

  if (error) {
    return (
      <p className="text-sm text-destructive" role="alert">
        {error}，请下载后打开。
      </p>
    );
  }
  if (text === null) {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        正在加载预览…
      </p>
    );
  }
  if (html) {
    // 使用无权限沙箱保留 CSS/SVG 动画；禁止脚本、表单、外部资源和访问工作台身份。
    const policy = "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; base-uri 'none'; form-action 'none'";
    return (
      <iframe
        className="h-[min(70vh,720px)] w-full rounded-lg border bg-white"
        referrerPolicy="no-referrer"
        sandbox=""
        srcDoc={`<meta http-equiv="Content-Security-Policy" content="${policy}">${text}`}
        title="HTML/SVG 预览"
      />
    );
  }
  if (markdown) {
    return (
      <Suspense fallback={<p className="text-sm text-muted-foreground">正在渲染…</p>}>
        <MessageResponse>{text}</MessageResponse>
      </Suspense>
    );
  }
  return (
    <pre className="overflow-auto whitespace-pre-wrap rounded-lg bg-muted/40 p-3 text-sm">
      {text}
    </pre>
  );
}

function DocxPreview({ href }: { href: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) {
      return;
    }
    const controller = new AbortController();
    host.replaceChildren();
    setError(null);
    setLoading(true);
    void (async () => {
      try {
        const response = await fetch(href, { signal: controller.signal });
        if (!response.ok) {
          throw new Error("预览加载失败");
        }
        const buffer = await response.arrayBuffer();
        const { renderAsync } = await import("docx-preview");
        if (controller.signal.aborted || !hostRef.current) {
          return;
        }
        await renderAsync(buffer, hostRef.current, undefined, {
          inWrapper: true,
          breakPages: true,
          ignoreHeight: true,
          className: "docx",
        });
        setLoading(false);
      } catch (cause) {
        if (cause instanceof DOMException && cause.name === "AbortError") {
          return;
        }
        setLoading(false);
        setError(cause instanceof Error ? cause.message : "预览加载失败");
      }
    })();
    return () => controller.abort();
  }, [href]);

  return (
    <div className="grid gap-2">
      {loading && !error ? (
        <p className="text-sm text-muted-foreground" role="status">
          正在渲染 Word 预览…
        </p>
      ) : null}
      {error ? (
        <p className="text-sm text-destructive" role="alert">
          {error}，请下载后用 Word 打开。
        </p>
      ) : null}
      <div
        className="docx-preview-host max-h-[min(70vh,720px)] overflow-auto rounded-lg bg-white text-black [&_.docx-wrapper]:bg-white [&_.docx-wrapper]:shadow-none"
        ref={hostRef}
        style={{ fontFamily: '"Noto Serif SC", "Source Han Serif SC", SimSun, serif' }}
      />
    </div>
  );
}
