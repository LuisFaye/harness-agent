import { Clock3, Paperclip, Pencil, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { QueuedMessage } from "./use-agent-workspace";

export function QueuedMessageCard({ message, onCancel, onEdit, onEditingChange }: {
  message: QueuedMessage;
  onCancel: () => void;
  onEdit: (query: string) => void;
  onEditingChange: (editing: boolean) => void;
}) {
  const [text, setText] = useState(message.query);
  return (
    <>
      <div aria-label="排队消息" aria-live="polite" className="composer-queued-message mx-1 mt-1 mb-1 flex min-w-0 items-center rounded-xl border border-primary/20 bg-primary-soft/65 py-1 pr-1 pl-2.5 shadow-2xs">
        <Clock3 aria-hidden="true" className="size-3.5 shrink-0 text-primary" />
        <span className="ml-1.5 shrink-0 text-xs font-semibold text-primary">{message.editing ? "编辑中" : "排队"}</span>
        <span className="ml-2 min-w-0 flex-1 truncate text-xs text-foreground/85" title={message.query || "仅发送附件"}>
          {message.query || "仅发送附件"}
        </span>
        {message.attachments.length ? (
          <span aria-label={`${message.attachments.length} 个附件`} className="ml-2 flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
            <Paperclip aria-hidden="true" className="size-3" />{message.attachments.length}
          </span>
        ) : null}
        <Button aria-label="编辑排队消息" size="icon-sm" type="button" variant="ghost" onClick={() => {
          setText(message.query);
          onEditingChange(true);
        }}>
          <Pencil aria-hidden="true" className="size-3.5" />
        </Button>
        <Button aria-label="取消排队消息" onClick={onCancel} size="icon-sm" type="button" variant="ghost">
          <X aria-hidden="true" className="size-3.5" />
        </Button>
      </div>
      <Dialog open={message.editing ?? false} onOpenChange={onEditingChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑排队消息</DialogTitle>
            <DialogDescription>编辑期间暂停自动发送，原有附件和输入框中的新草稿保持不变。</DialogDescription>
          </DialogHeader>
          <label className="sr-only" htmlFor="queued-message-text">排队消息内容</label>
          <textarea id="queued-message-text" className="min-h-32 w-full rounded-lg border bg-background p-3 text-base" value={text} onChange={(event) => setText(event.target.value)} />
          {message.attachments.length ? <p className="text-sm text-muted-foreground">保留 {message.attachments.length} 个附件</p> : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onEditingChange(false)}>放弃修改</Button>
            <Button type="button" disabled={!text.trim() && !message.attachments.length} onClick={() => onEdit(text)}>保存并继续排队</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
