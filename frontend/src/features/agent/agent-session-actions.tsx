import { Download, Share2, Star } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { createSessionShare } from "@/features/sharing/sharing-api";
import { ApiError } from "@/lib/api/client";
import type { ConversationMessage } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

interface AgentSessionActionsProps {
  sessionId: string;
  title: string;
  hasMessages: boolean;
  isFavorite: boolean;
  messages: ConversationMessage[];
  onToggleFavorite: () => boolean;
}

type NoticeTone = "success" | "error";

export function AgentSessionActions({
  sessionId,
  title,
  hasMessages,
  isFavorite,
  messages,
  onToggleFavorite,
}: AgentSessionActionsProps) {
  const [notice, setNotice] = useState<{ text: string; tone: NoticeTone } | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [recipientEmail, setRecipientEmail] = useState("");
  const [sharing, setSharing] = useState(false);
  const noticeTimerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (noticeTimerRef.current !== null) {
      window.clearTimeout(noticeTimerRef.current);
    }
  }, []);

  function announce(text: string, tone: NoticeTone) {
    setNotice({ text, tone });
    if (noticeTimerRef.current !== null) {
      window.clearTimeout(noticeTimerRef.current);
    }
    noticeTimerRef.current = window.setTimeout(() => setNotice(null), 2600);
  }

  async function shareSession(event: FormEvent) {
    event.preventDefault();
    setSharing(true);
    try {
      await createSessionShare(sessionId, recipientEmail);
      setRecipientEmail("");
      setShareOpen(false);
      announce("已只读分享给指定成员", "success");
    } catch (error) {
      announce(error instanceof ApiError ? error.message : "分享失败，请重试。", "error");
    } finally {
      setSharing(false);
    }
  }

  function toggleFavorite() {
    const saved = onToggleFavorite();
    announce(saved ? "收藏已更新" : "本地收藏保存失败", saved ? "success" : "error");
  }

  function exportSessionMarkdown() {
    try {
      const exportedAt = new Date();
      const lines: string[] = [
        `# ${title || "未命名对话"}`,
        "",
        `- 导出时间：${exportedAt.toISOString()}`,
        "- 范围：仅包含页面已加载的最近最多 200 条消息",
        "",
      ];
      for (const message of messages) {
        const role = message.role === "user"
          ? "用户"
          : message.role === "assistant"
            ? "SB Agent"
            : "系统";
        lines.push(`## ${role}`, "", message.content, "");
      }
      const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `${safeFileName(title || "未命名对话")}-${exportedAt.toISOString().slice(0, 10)}.md`;
      link.click();
      URL.revokeObjectURL(link.href);
      announce("会话已导出", "success");
    } catch {
      announce("导出失败，请重试。", "error");
    }
  }

  return (
    <div className="relative shrink-0">
      <div className="flex items-center gap-1">
        <Button
          aria-label={isFavorite ? "取消本地收藏" : "添加到本地收藏"}
          className={cn(isFavorite && "bg-primary-soft text-primary")}
          disabled={!hasMessages}
          onClick={toggleFavorite}
          size="icon"
          variant="ghost"
        >
          <Star
            aria-hidden="true"
            className={cn("size-5", isFavorite && "fill-current")}
          />
        </Button>
        <Button
          aria-expanded={shareOpen}
          aria-label="分享会话给成员"
          disabled={!hasMessages}
          onClick={() => setShareOpen((current) => !current)}
          size="icon"
          variant="ghost"
        >
          <Share2 aria-hidden="true" />
        </Button>
        <Button
          aria-label="导出会话 Markdown"
          disabled={!hasMessages}
          onClick={exportSessionMarkdown}
          size="icon"
          variant="ghost"
        >
          <Download aria-hidden="true" />
        </Button>
      </div>
      {shareOpen ? (
        <form className="absolute top-full right-0 z-30 mt-2 w-72 rounded-xl border bg-background p-3 shadow-lg" onSubmit={shareSession}>
          <label className="text-xs font-medium" htmlFor="share-recipient-email">接收成员邮箱</label>
          <input
            autoFocus
            className="mt-2 h-10 w-full rounded-lg border bg-background px-3 text-sm outline-none focus:ring-2 focus:ring-ring"
            id="share-recipient-email"
            onChange={(event) => setRecipientEmail(event.target.value)}
            placeholder="friend@example.com"
            required
            type="email"
            value={recipientEmail}
          />
          <p className="mt-2 text-xs text-muted-foreground">对方只能查看消息和产物；继续对话时会复制到自己的空间。</p>
          <Button className="mt-3 w-full" disabled={sharing} size="sm" type="submit">确认只读分享</Button>
        </form>
      ) : null}
      <p
        aria-live="polite"
        className={cn(
          "absolute top-full right-0 z-20 mt-1.5 rounded-lg border bg-background px-3 py-1.5 text-xs shadow-sm whitespace-nowrap",
          notice?.tone === "error"
            ? "border-destructive/30 text-destructive"
            : notice
              ? "border-success/30 text-success"
              : "pointer-events-none opacity-0",
        )}
        role="status"
      >
        {notice?.text ?? ""}
      </p>
    </div>
  );
}

function safeFileName(value: string): string {
  const cleaned = value
    .replace(/[\\/:*?"<>|\s]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return cleaned || "未命名对话";
}
