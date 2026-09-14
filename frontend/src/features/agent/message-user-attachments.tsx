import { FileDown, FileText } from "lucide-react";
import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiUrl } from "@/lib/api/client";
import type { Attachment, ConversationMessage } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

export interface MessageAttachmentView extends Attachment {
  preview_url?: string;
}

interface MessageUserAttachmentsProps {
  attachments: MessageAttachmentView[];
  className?: string;
}

export function MessageUserAttachments({ attachments, className }: MessageUserAttachmentsProps) {
  const [previewAttachment, setPreviewAttachment] = useState<MessageAttachmentView | null>(null);

  if (!attachments.length) {
    return null;
  }

  const previewHref = previewAttachment
    ? apiUrl(previewAttachment.download_url)
    : "";
  const previewSrc = previewAttachment?.preview_url || previewHref;

  return (
    <>
      <div
        aria-label="消息附件"
        className={cn("mb-2 flex flex-wrap gap-2", className)}
      >
        {attachments.map((attachment) => {
          const href = apiUrl(attachment.download_url);
          const thumbnailSrc = attachment.preview_url || href;
          const image = isImageAttachment(attachment);
          const commonClass = "group flex max-w-full items-center gap-2 rounded-xl border border-border/70 bg-background/80 px-2 py-1.5 text-left text-xs transition-colors hover:border-primary/35 hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
          const label = (
            <>
              {image ? (
                <img
                  alt=""
                  className="size-14 shrink-0 rounded-lg object-cover"
                  loading="lazy"
                  src={thumbnailSrc}
                />
              ) : (
                <span className="flex size-14 shrink-0 items-center justify-center rounded-lg bg-muted">
                  <FileText aria-hidden="true" className="size-5 text-muted-foreground" />
                </span>
              )}
              <span className="min-w-0">
                <strong className="block max-w-40 truncate font-medium">{attachment.filename}</strong>
                <span className="block text-muted-foreground">
                  {image ? "点击预览" : "点击下载"}
                </span>
              </span>
            </>
          );

          if (image) {
            return (
              <button
                className={commonClass}
                key={attachment.id}
                onClick={() => setPreviewAttachment(attachment)}
                title={attachment.filename}
                type="button"
              >
                {label}
              </button>
            );
          }

          return (
            <a
              className={commonClass}
              href={href}
              key={attachment.id}
              rel="noreferrer"
              target="_blank"
              title={attachment.filename}
            >
              {label}
            </a>
          );
        })}
      </div>

      <Dialog
        onOpenChange={(open) => {
          if (!open) {
            setPreviewAttachment(null);
          }
        }}
        open={previewAttachment !== null}
      >
        <DialogContent className="flex max-h-[calc(100svh-2rem)] flex-col overflow-hidden sm:max-w-4xl">
          <DialogHeader className="pr-8">
            <DialogTitle className="truncate" title={previewAttachment?.filename}>
              {previewAttachment?.filename}
            </DialogTitle>
          </DialogHeader>
          <div className="min-h-0 min-w-0 flex-1 overflow-auto">
            {previewAttachment ? (
              <img
                alt={previewAttachment.filename}
                className="mx-auto max-h-[min(70vh,720px)] max-w-full rounded-lg object-contain"
                src={previewSrc}
              />
            ) : null}
          </div>
          <DialogFooter>
            <a
              className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-border bg-background px-3 text-sm font-medium hover:bg-muted"
              href={previewHref}
              rel="noopener noreferrer"
              target="_blank"
            >
              <FileDown aria-hidden="true" className="size-4" />
              下载原图
            </a>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function resolveMessageAttachments(
  message: ConversationMessage,
  sessionAttachments: Attachment[],
): MessageAttachmentView[] {
  const inlineAttachments = message.attachments as MessageAttachmentView[] | undefined;
  if (inlineAttachments?.length) {
    return inlineAttachments;
  }
  const ids = message.attachment_ids ?? [];
  if (!ids.length) {
    return [];
  }
  const lookup = new Map(sessionAttachments.map((item) => [item.id, item]));
  return ids
    .map((id) => lookup.get(id))
    .filter((item): item is Attachment => Boolean(item));
}

function isImageAttachment(attachment: Attachment): boolean {
  if (attachment.content_type.startsWith("image/")) {
    return true;
  }
  return [".png", ".jpg", ".jpeg", ".webp", ".gif"].includes(attachment.suffix.toLowerCase());
}
