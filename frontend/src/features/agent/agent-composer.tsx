import "./conversation.css";

import {
  Clock3,
  FileText,
  LoaderCircle,
  Paperclip,
  Pencil,
  ArrowUp,
  Square,
  X,
} from "lucide-react";
import {
  type ChangeEvent,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  PromptInput,
  PromptInputBody,
  PromptInputButton,
  PromptInputFooter,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-provider";
import type { RunUsage } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

import { isImageFile } from "./attachment-preview";
import { ContextUsageDialog } from "./context-usage-dialog";
import { SlashSkillMenu, useSlashSkillMenu } from "./slash-skill-menu";
import type { PendingAttachment, QueuedMessage } from "./use-agent-workspace";

interface AgentComposerProps {
  suggestedPrompt?: { text: string; revision: number } | null;
  sessionId: string;
  pendingAttachments: PendingAttachment[];
  contextObservation?: Record<string, unknown> | null;
  latestRunUsage?: RunUsage | null;
  queuedMessage?: QueuedMessage | null;
  isRunning: boolean;
  isUploading: boolean;
  onCancelQueued?: () => void;
  onEditQueued?: () => void;
  onSend: (query: string) => Promise<void>;
  onStop: () => Promise<void>;
  onUpload: (files: File[]) => Promise<void>;
  onRemoveAttachment: (localId: string) => void;
}

export function AgentComposer({
  suggestedPrompt,
  sessionId,
  pendingAttachments,
  contextObservation = null,
  latestRunUsage = null,
  queuedMessage = null,
  isRunning,
  isUploading,
  onCancelQueued = () => {},
  onEditQueued = () => {},
  onSend,
  onStop,
  onUpload,
  onRemoveAttachment,
}: AgentComposerProps) {
  const { user } = useAuth();
  const [draft, setDraft] = useState("");
  const [cursorPosition, setCursorPosition] = useState(0);
  const composingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const storageKey = `sb-agent-draft:v2:${user!.id}:${sessionId}`;
  const readyAttachmentCount = pendingAttachments.filter(
    (item) => item.attachment?.id && !item.error,
  ).length;
  const hasUploadingAttachment = pendingAttachments.some((item) => item.uploading);
  const canSend = Boolean(draft.trim() || readyAttachmentCount) && !hasUploadingAttachment;

  useEffect(() => {
    setDraft(window.localStorage.getItem(storageKey) ?? "");
  }, [storageKey]);

  useEffect(() => {
    if (!suggestedPrompt) return;
    setDraft(suggestedPrompt.text);
    window.localStorage.setItem(storageKey, suggestedPrompt.text);
    textareaRef.current?.focus();
  }, [suggestedPrompt, storageKey]);

  function updateDraft(value: string) {
    setDraft(value);
    window.localStorage.setItem(storageKey, value);
  }

  function syncCursorPosition() {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    setCursorPosition(textarea.selectionStart ?? textarea.value.length);
  }

  function restoreCursorPosition(position: number) {
    window.requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) {
        return;
      }
      textarea.focus();
      textarea.setSelectionRange(position, position);
      setCursorPosition(position);
    });
  }

  const slashSkillMenu = useSlashSkillMenu({
    cursorPosition,
    draft,
    onCursorRestore: restoreCursorPosition,
    onDraftChange: updateDraft,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSend || queuedMessage) {
      return;
    }
    const query = draft.trim();
    updateDraft("");
    void onSend(query);
  }

  function editQueuedMessage() {
    if (!queuedMessage) {
      return;
    }
    updateDraft(queuedMessage.query);
    onEditQueued();
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (!composingRef.current && slashSkillMenu.handleKeyDown(event)) {
      return;
    }
    const nativeEvent = event.nativeEvent;
    if (
      event.key === "Enter"
      && !event.shiftKey
      && !composingRef.current
      && !nativeEvent.isComposing
      && nativeEvent.keyCode !== 229
    ) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const imageFiles = extractClipboardImages(event.clipboardData);
    if (!imageFiles.length) {
      return;
    }
    event.preventDefault();
    void onUpload(imageFiles);
  }

  function handleFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    void onUpload(files);
  }

  return (
    <div className="conversation-composer shrink-0 px-4 pt-3 sm:px-6">
      <label className="sr-only" htmlFor="agent-message">
        发送给 SB Agent
      </label>
      <PromptInput
        className="composer-shadow relative rounded-2xl border-border/70 bg-card p-2 focus-within:border-primary/45 focus-within:ring-2 focus-within:ring-primary/20"
        onSubmit={submit}
      >
        {queuedMessage ? (
          <div
            aria-label="排队消息"
            aria-live="polite"
            className="composer-queued-message mx-1 mt-1 flex min-w-0 items-center rounded-lg bg-primary-soft/55 pl-2"
          >
            <Clock3 aria-hidden="true" className="size-3.5 shrink-0 text-primary" />
            <span className="ml-1.5 shrink-0 text-xs font-semibold text-primary">排队</span>
            <span
              className="ml-2 min-w-0 flex-1 truncate text-xs text-foreground/85"
              title={queuedMessage.query || "仅发送附件"}
            >
              {queuedMessage.query || "仅发送附件"}
            </span>
            {queuedMessage.attachments.length ? (
              <span
                aria-label={`${queuedMessage.attachments.length} 个附件`}
                className="ml-2 flex shrink-0 items-center gap-1 text-xs text-muted-foreground"
                title={queuedMessage.attachments.map((item) => item.attachment?.filename || item.file.name).join("、")}
              >
                <Paperclip aria-hidden="true" className="size-3" />
                {queuedMessage.attachments.length}
              </span>
            ) : null}
            <div className="ml-1 flex shrink-0 items-center">
              <Button
                aria-label="编辑排队消息"
                onClick={editQueuedMessage}
                size="icon"
                title="编辑排队消息"
                type="button"
                variant="ghost"
              >
                <Pencil aria-hidden="true" className="size-3.5" />
              </Button>
              <Button
                aria-label="取消排队消息"
                onClick={onCancelQueued}
                size="icon"
                title="取消排队消息"
                type="button"
                variant="ghost"
              >
                <X aria-hidden="true" className="size-3.5" />
              </Button>
            </div>
          </div>
        ) : null}
        {pendingAttachments.length ? (
          <div aria-label="待发附件" className="composer-attachments flex flex-wrap gap-2 px-1 pt-1">
            {pendingAttachments.map((item) => {
              const isImage = isImageFile(item.file);
              const label = item.attachment?.filename || item.file.name || "未命名附件";
              const status = item.error
                ? item.error
                : item.uploading
                  ? "上传中"
                  : item.attachment?.parse_status === "success"
                    ? "已就绪"
                    : item.attachment?.parse_message || "等待上传";
              return (
                <div
                  className={cn(
                    "composer-attachment-chip relative flex max-w-full items-center gap-2 rounded-xl border px-2 py-1.5 text-left text-xs",
                    item.error ? "border-destructive/40 bg-destructive/5" : "border-border bg-muted/30",
                  )}
                  key={item.localId}
                >
                  {isImage ? (
                    <img
                      alt=""
                      className="size-10 shrink-0 rounded-lg object-cover"
                      src={item.previewUrl}
                    />
                  ) : (
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-background">
                      <FileText aria-hidden="true" className="size-4 text-muted-foreground" />
                    </span>
                  )}
                  <span className="min-w-0 flex-1 pr-5">
                    <strong className="block truncate font-medium">{label}</strong>
                    <span className="block truncate text-muted-foreground">{status}</span>
                  </span>
                  {item.uploading ? (
                    <LoaderCircle
                      aria-hidden="true"
                      className="absolute right-2 top-2 size-3.5 animate-spin text-muted-foreground"
                    />
                  ) : (
                    <button
                      aria-label={`移除附件 ${label}`}
                      className="absolute right-1 top-1 rounded-md p-1 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                      onClick={() => onRemoveAttachment(item.localId)}
                      type="button"
                    >
                      <X aria-hidden="true" className="size-3.5" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        ) : null}
        {slashSkillMenu.isOpen ? (
          <SlashSkillMenu
            highlightedIndex={slashSkillMenu.highlightedIndex}
            isLoading={slashSkillMenu.isLoading}
            items={slashSkillMenu.items}
            onHighlight={slashSkillMenu.setHighlightedIndex}
            onSelect={slashSkillMenu.insertSkill}
          />
        ) : null}
        <PromptInputBody>
          <PromptInputTextarea
            disabled={queuedMessage !== null}
            id="agent-message"
            ref={textareaRef}
            onChange={(event) => {
              updateDraft(event.target.value);
              setCursorPosition(event.target.selectionStart ?? event.target.value.length);
            }}
            onClick={syncCursorPosition}
            onFocus={syncCursorPosition}
            onKeyUp={syncCursorPosition}
            onSelect={syncCursorPosition}
            onCompositionEnd={() => {
              composingRef.current = false;
            }}
            onCompositionStart={() => {
              composingRef.current = true;
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={queuedMessage
              ? "编辑或取消排队消息后可继续输入"
              : isRunning
                ? "继续输入，按 Enter 排队发送"
                : "描述你的问题或任务，可直接粘贴截图"}
            rows={2}
            value={draft}
          />
        </PromptInputBody>
        <PromptInputFooter>
          <PromptInputTools className="min-w-0 flex-1 flex-wrap">
            <ContextUsageDialog
              contextObservation={contextObservation}
              usage={latestRunUsage}
            />
            <input
              accept=".txt,.md,.csv,.json,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.webp,.gif,image/*"
              aria-label="选择要上传的附件"
              className="sr-only"
              id="agent-attachments"
              multiple
              onChange={handleFiles}
              ref={fileInputRef}
              type="file"
            />
            <PromptInputButton
              aria-label="添加附件"
              disabled={isUploading || queuedMessage !== null}
              onClick={() => fileInputRef.current?.click()}
              size="icon"
            >
              {isUploading
                ? <LoaderCircle aria-hidden="true" className="animate-spin" />
                : <Paperclip aria-hidden="true" />}
            </PromptInputButton>
          </PromptInputTools>
          {isRunning ? (
            <div className="flex items-center gap-2">
              <Button aria-label="停止当前任务" onClick={() => void onStop()} type="button" variant="outline">
                <Square aria-hidden="true" />
                停止
              </Button>
              {!queuedMessage ? (
                <Button
                  aria-label="排队发送消息"
                  className="primary-gradient shadow-sm shadow-primary/25"
                  disabled={!canSend}
                  type="submit"
                >
                  <Clock3 aria-hidden="true" />
                  排队
                </Button>
              ) : null}
            </div>
          ) : queuedMessage ? (
            <Button disabled type="button" variant="outline">
              <Clock3 aria-hidden="true" />
              已排队
            </Button>
          ) : (
            <Button
              aria-label="发送消息"
              className="primary-gradient shadow-sm shadow-primary/25"
              disabled={!canSend}
              type="submit"
            >
              <ArrowUp aria-hidden="true" />
              发送
            </Button>
          )}
        </PromptInputFooter>
      </PromptInput>
      <p className="mt-2 flex flex-wrap justify-between gap-x-4 gap-y-1 px-1 text-xs text-muted-foreground">
        <span>输入 / 可插入技能，支持 Ctrl+V 粘贴截图，附件仅随本条消息发送</span>
        <span className="hidden sm:inline">
          {queuedMessage
            ? "当前任务结束后自动发送"
            : isRunning
              ? "Enter 排队 · Shift + Enter 换行"
              : "Enter 发送 · Shift + Enter 换行"}
        </span>
      </p>
    </div>
  );
}

function extractClipboardImages(clipboardData: DataTransfer | null): File[] {
  if (!clipboardData) {
    return [];
  }
  const files: File[] = [];
  for (const item of Array.from(clipboardData.items)) {
    if (item.kind !== "file" || !item.type.startsWith("image/")) {
      continue;
    }
    const file = item.getAsFile();
    if (!file) {
      continue;
    }
    const extension = file.type.split("/")[1] || "png";
    const filename = file.name && file.name.includes(".")
      ? file.name
      : `pasted-image-${Date.now()}.${extension}`;
    files.push(file.name === filename ? file : new File([file], filename, { type: file.type }));
  }
  return files;
}
