import "./conversation.css";

import {
  ArrowUp,
  Clock3,
  FileText,
  LoaderCircle,
  Paperclip,
  RotateCcw,
  Square,
  X,
} from "lucide-react";
import {
  type ChangeEvent,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useLayoutEffect,
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
import { QueuedMessageCard } from "./queued-message-card";
import { SlashSkillMenu, useSlashSkillMenu } from "./slash-skill-menu";
import type { PendingAttachment, QueuedMessage } from "./use-agent-workspace";

interface AgentComposerProps {
  variant?: "welcome" | "dock";
  suggestedPrompt?: { text: string; revision: number } | null;
  sessionId: string;
  pendingAttachments: PendingAttachment[];
  contextObservation?: Record<string, unknown> | null;
  latestRunUsage?: RunUsage | null;
  queuedMessage?: QueuedMessage | null;
  isRunning: boolean;
  isUploading: boolean;
  isStopping?: boolean;
  uploadNotice?: string | null;
  onRetryAttachment?: (localId: string) => Promise<void>;
  onCancelUpload?: (localId: string) => void;
  onQueuedEditingChange?: (editing: boolean) => void;
  onCancelQueued?: () => void;
  onEditQueued?: (query: string) => void;
  onSend: (query: string) => Promise<void>;
  onStop: () => Promise<void>;
  onUpload: (files: File[]) => Promise<void>;
  onRemoveAttachment: (localId: string) => void;
}

export function AgentComposer({
  variant = "dock",
  suggestedPrompt,
  sessionId,
  pendingAttachments,
  contextObservation = null,
  latestRunUsage = null,
  queuedMessage = null,
  isRunning,
  isUploading,
  isStopping = false,
  uploadNotice = null,
  onRetryAttachment = async () => {},
  onCancelUpload = () => {},
  onQueuedEditingChange = () => {},
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
  const consumedSuggestionRef = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const storageKey = `sb-agent-draft:v2:${user!.id}:${sessionId}`;
  const readyAttachmentCount = pendingAttachments.filter(
    (item) => item.attachment?.id && !item.error,
  ).length;
  const hasUnavailableAttachment = pendingAttachments.some((item) => item.uploading || item.error);
  const canSend = Boolean(draft.trim() || readyAttachmentCount) && !hasUnavailableAttachment;
  const isWelcome = variant === "welcome";

  useLayoutEffect(() => {
    try {
      setDraft(window.localStorage.getItem(storageKey) ?? "");
    } catch {
      setDraft("");
    }
    setCursorPosition(0);
  }, [storageKey]);

  useEffect(() => {
    if (!suggestedPrompt || consumedSuggestionRef.current === suggestedPrompt.revision) return;
    consumedSuggestionRef.current = suggestedPrompt.revision;
    updateDraft(suggestedPrompt.text);
    textareaRef.current?.focus();
  }, [suggestedPrompt, storageKey]);

  function updateDraft(value: string) {
    setDraft(value);
    try {
      window.localStorage.setItem(storageKey, value);
    } catch {
      // 存储不可用时仍允许继续输入和发送。
    }
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
    <div
      className={cn(
        "conversation-composer shrink-0",
        isWelcome ? "mx-auto w-full max-w-[660px] pb-4" : "px-4 pt-1 pb-2 sm:px-6",
      )}
    >
      <label className="sr-only" htmlFor="agent-message">
        发送给 SB Agent
      </label>
      <div className="relative">
        <PromptInput
          className={cn(
            "composer-shadow relative focus-within:border-sky-400/60 focus-within:ring-2 focus-within:ring-sky-400/15",
            isWelcome ? "rounded-2xl p-3 sm:p-3.5" : "rounded-2xl px-3 py-2",
          )}
          onSubmit={submit}
        >
          {queuedMessage ? (
            <QueuedMessageCard
              key={queuedMessage.id}
              message={queuedMessage}
              onCancel={onCancelQueued}
              onEdit={onEditQueued}
              onEditingChange={onQueuedEditingChange}
            />
          ) : null}
          {uploadNotice ? <p className="px-2 py-1 text-xs text-warning" role="status">{uploadNotice}</p> : null}
          {pendingAttachments.length ? (
            <div aria-label="待发附件" className="composer-attachments flex flex-wrap gap-1.5 px-1 pt-0.5">
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
                      "composer-attachment-chip relative flex max-w-full items-center gap-2 rounded-lg border border-border/75 bg-background/80 px-2 py-1 text-left text-xs",
                      item.error && "border-destructive/40 bg-destructive/5",
                    )}
                    key={item.localId}
                  >
                    {isImage && item.previewUrl ? (
                      <img
                        alt=""
                        className="size-7 shrink-0 rounded-md object-cover"
                        src={item.previewUrl}
                      />
                    ) : (
                      <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/70">
                        <FileText aria-hidden="true" className="size-3.5 text-primary" />
                      </span>
                    )}
                    <span className="min-w-0 flex-1">
                      <strong className="block truncate text-[11.5px] font-medium text-foreground">{label}</strong>
                      <span className="block break-words text-[10.5px] text-muted-foreground" role={item.error ? "alert" : "status"}>{status}</span>
                    </span>
                    <div className="flex shrink-0 items-center gap-0.5">
                      {item.error ? (
                        <Button aria-label={`重试上传 ${label}`} onClick={() => void onRetryAttachment(item.localId)} size="icon-xs" type="button" variant="ghost">
                          <RotateCcw aria-hidden="true" className="size-3" />
                        </Button>
                      ) : null}
                      <Button
                        aria-label={item.uploading ? `取消上传 ${label}` : `移除附件 ${label}`}
                        onClick={() => item.uploading ? onCancelUpload(item.localId) : onRemoveAttachment(item.localId)}
                        size="icon-xs" type="button" variant="ghost"
                      >
                        <X aria-hidden="true" className="size-3" />
                      </Button>
                    </div>
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
                ? "可继续起草，排队消息发送后可提交下一条…"
                : isRunning
                  ? "继续输入，按 Enter 排队发送…"
                  : "输入问题，或输入 / 调用技能…"}
              rows={1}
              value={draft}
            />
          </PromptInputBody>
          <PromptInputFooter className="mt-1 min-h-7">
            <PromptInputTools className="min-w-0 flex-1 items-center gap-1">
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
                className="size-7 rounded-lg text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                onClick={() => fileInputRef.current?.click()}
                size="icon-sm"
                title="添加附件（支持粘贴截图）"
              >
                {isUploading
                  ? <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" />
                  : <Paperclip aria-hidden="true" className="size-3.5" />}
              </PromptInputButton>
              <button
                aria-label="插入斜杠技能"
                className="grid size-7 shrink-0 place-items-center rounded-lg font-mono text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
                onClick={() => {
                  const next = draft.endsWith("/") ? draft : `${draft}${draft && !draft.endsWith(" ") ? " " : ""}/`;
                  updateDraft(next);
                  restoreCursorPosition(next.length);
                }}
                title="输入 / 调用技能"
                type="button"
              >
                /
              </button>
              <ContextUsageDialog
                contextObservation={contextObservation}
                usage={latestRunUsage}
              />
            </PromptInputTools>

            <div className="flex shrink-0 items-center gap-1.5">
              {isRunning ? (
                <div className="flex items-center gap-1.5">
                  <Button aria-label={isStopping ? "正在停止当前任务" : "停止当前任务"} disabled={isStopping} onClick={() => void onStop()} size="sm" type="button" variant="outline">
                    {isStopping ? <LoaderCircle aria-hidden="true" className="animate-spin" /> : <Square aria-hidden="true" />}
                    {isStopping ? "停止中…" : "停止"}
                  </Button>
                  {!queuedMessage ? (
                    <Button
                      aria-label="排队发送消息"
                      className="rounded-full"
                      disabled={!canSend}
                      size="sm"
                      type="submit"
                    >
                      <Clock3 aria-hidden="true" />
                      排队
                    </Button>
                  ) : null}
                </div>
              ) : queuedMessage ? (
                <Button disabled size="sm" type="button" variant="outline">
                  <Clock3 aria-hidden="true" />
                  已排队
                </Button>
              ) : (
                <Button
                  aria-label="发送消息"
                  title="发送消息 (Enter)"
                  className="size-7.5 rounded-full p-0"
                  disabled={!canSend}
                  size="icon-sm"
                  type="submit"
                >
                  <ArrowUp aria-hidden="true" className="size-3.5" />
                </Button>
              )}
            </div>
          </PromptInputFooter>
        </PromptInput>
      </div>
      {pendingAttachments.some((item) => item.error) ? (
        <p className="mt-1 px-2 text-[11px] text-destructive">
          请重试或移除未就绪附件后发送
        </p>
      ) : null}
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
