import {
  Check,
  ChevronDown,
  Copy,
  Download,
  LoaderCircle,
  RotateCcw,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  Message,
  MessageAction,
  MessageActions,
  MessageContent,
} from "@/components/ai-elements/message";
import { AgentMark } from "@/components/layout/agent-mark";
import { Button } from "@/components/ui/button";
import {
  MessageScroller as MessageScrollerRoot,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { getRun } from "@/lib/api/agent";
import { useAuth } from "@/features/auth/auth-provider";
import { ApiError } from "@/lib/api/client";
import type { Attachment, Artifact, ConversationMessage } from "@/lib/api/schemas";
import type { DatabaseConnectionWritePayload } from "@/lib/api/management";
import { historicalRunProjection } from "@/features/runs/run-format";
import type { RunProjection } from "@/lib/stream/run-projection";
import {
  artifactsForRun,
  MessageArtifacts,
} from "@/features/artifacts/message-artifacts";

import {
  MessageUserAttachments,
  resolveMessageAttachments,
} from "./message-user-attachments";
import { ConversationWelcome } from "./conversation-welcome";
import { ExecutionTimeline } from "./execution-timeline";
import { RunInteractionCard } from "./run-interaction-card";

const CitationMessageResponse = lazy(async () => ({
  default: (await import("@/features/citations/citation-message-response")).CitationMessageResponse,
}));

/** 实时运行占位消息的固定 id，与 run_id 组合保证 React key 稳定。 */
const LIVE_ASSISTANT_MESSAGE_ID = -9001;

interface MessageScrollerProps {
  onSelectPrompt?: (prompt: string) => void;
  messages: ConversationMessage[];
  sessionAttachments?: Attachment[];
  artifacts?: Artifact[];
  loading: boolean;
  error: unknown;
  projection: RunProjection | null;
  errorMessage: string | null;
  isRunning: boolean;
  onConfirm: () => void;
  onCancelConfirmation: () => void;
  onRetry: () => void;
  onDismissError: () => void;
  onDatabaseConnect?: (payload: DatabaseConnectionWritePayload, requestId: string) => Promise<void>;
  canRegenerate?: boolean;
  onRegenerate?: () => void;
}

export function MessageScroller({
  onSelectPrompt,
  messages,
  sessionAttachments = [],
  artifacts = [],
  loading,
  error,
  projection,
  errorMessage,
  isRunning,
  onConfirm,
  onCancelConfirmation,
  onRetry,
  onDismissError,
  onDatabaseConnect,
  canRegenerate = false,
  onRegenerate,
}: MessageScrollerProps) {
  const displayMessages = useMemo(
    () => buildDisplayMessages(messages, projection),
    [messages, projection],
  );
  let lastAssistantMessageId: number | null = null;
  let lastUserMessageKey: string | null = null;
  for (const message of displayMessages) {
    if (message.role === "assistant") {
      lastAssistantMessageId = message.id;
    }
    if (message.role === "user") {
      lastUserMessageKey = messageIdentity(message);
    }
  }
  const showHistoryLoading = loading && displayMessages.length === 0 && !projection;
  const showInteractionOnly = projection
    ? hasVisibleRunInteraction(projection, errorMessage)
      && !displayMessages.some((message) => (
        message.role === "assistant" && message.run_id === projection.runId
      ))
    : Boolean(errorMessage);

  return (
    <MessageScrollerProvider
      autoScroll
      defaultScrollPosition="end"
      scrollEdgeThreshold={80}
      scrollPreviousItemPeek={64}
    >
      <MessageScrollerRoot>
        <MessageScrollerViewport aria-label="会话消息">
          <MessageScrollerContent
            aria-busy={isRunning}
            className="pt-6 sm:pt-8"
          >
            {showHistoryLoading ? (
              <MessageScrollerItem messageId="loading">
                <MessageSkeleton />
              </MessageScrollerItem>
            ) : null}
            {error ? (
              <MessageScrollerItem messageId="history-error">
                <div className="mx-auto w-full max-w-[860px] px-4 pt-6 sm:px-6">
                  <p className="rounded-xl bg-destructive/10 p-4 text-sm text-destructive" role="alert">
                    消息历史加载失败，新消息暂时不能安全地关联上下文。
                  </p>
                </div>
              </MessageScrollerItem>
            ) : null}
            {!showHistoryLoading && !error && displayMessages.length === 0 && !projection ? (
              <MessageScrollerItem messageId="empty">
                <ConversationWelcome onSelect={onSelectPrompt ?? (() => {})} />
              </MessageScrollerItem>
            ) : null}
            {displayMessages.map((message) => {
              const liveProjection = resolveLiveProjection(message, projection, isRunning);
              const messageKey = messageIdentity(message);
              return (
                <MessageScrollerItem
                  key={messageKey}
                  messageId={messageKey}
                  scrollAnchor={messageKey === lastUserMessageKey}
                >
                  <div className="mx-auto w-full max-w-[860px] px-4 sm:px-6">
                    <MessageItem
                      artifacts={artifactsForRun(artifacts, message.run_id)}
                      canRegenerate={canRegenerate}
                      errorMessage={errorMessage}
                      isLastAssistant={message.role === "assistant" && message.id === lastAssistantMessageId}
                      isRunning={isRunning}
                      liveProjection={liveProjection}
                      message={message}
                      messageAttachments={resolveMessageAttachments(message, sessionAttachments)}
                      onCancelConfirmation={onCancelConfirmation}
                      onConfirm={onConfirm}
                      onDismissError={onDismissError}
                      onRetry={onRetry}
                      {...(onDatabaseConnect ? { onDatabaseConnect } : {})}
                      {...(onRegenerate ? { onRegenerate } : {})}
                    />
                  </div>
                </MessageScrollerItem>
              );
            })}
            {showInteractionOnly ? (
              <MessageScrollerItem messageId={`run:${projection?.runId || "interaction"}`}>
                <section
                  aria-label="当前运行"
                  className="mx-auto mt-1 w-full max-w-[860px] border-t px-4 pt-4 sm:px-6"
                >
                  <RunInteractionCard
                    busy={isRunning}
                    errorMessage={errorMessage}
                    onCancel={onCancelConfirmation}
                    onConfirm={onConfirm}
                    onDismissError={onDismissError}
                    {...(onDatabaseConnect ? { onDatabaseConnect } : {})}
                    onRetry={onRetry}
                    projection={projection}
                  />
                </section>
              </MessageScrollerItem>
            ) : null}
            <div aria-hidden="true" className="h-6 shrink-0" />
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton />
      </MessageScrollerRoot>
    </MessageScrollerProvider>
  );
}

function MessageItem({
  message,
  messageAttachments,
  artifacts,
  errorMessage,
  isLastAssistant,
  canRegenerate,
  isRunning,
  liveProjection,
  onCancelConfirmation,
  onConfirm,
  onDatabaseConnect,
  onDismissError,
  onRegenerate,
  onRetry,
}: {
  message: ConversationMessage;
  messageAttachments: ReturnType<typeof resolveMessageAttachments>;
  artifacts: Artifact[];
  errorMessage: string | null;
  isLastAssistant: boolean;
  canRegenerate: boolean;
  isRunning: boolean;
  liveProjection: RunProjection | null;
  onCancelConfirmation: () => void;
  onConfirm: () => void;
  onDatabaseConnect?: (payload: DatabaseConnectionWritePayload, requestId: string) => Promise<void>;
  onDismissError: () => void;
  onRegenerate?: () => void;
  onRetry: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const isLiveAssistant = Boolean(liveProjection);
  const isStreamOrigin = liveProjection?.origin === "stream";
  const answerSettled = isStreamOrigin && Boolean(liveProjection?.responseCompleted);
  const answerActive = Boolean(isStreamOrigin && !answerSettled
    && liveProjection && ["running", "responding"].includes(liveProjection.status));
  const answerText = liveProjection
    ? liveProjection.streamedText
    : message.content;
  const showStreamingAnswer = isLiveAssistant && Boolean(answerText);
  const showPersistedAnswer = !isLiveAssistant && Boolean(message.content);
  const showAnswer = showStreamingAnswer || showPersistedAnswer;

  async function copyMessage() {
    const text = isLiveAssistant ? liveProjection!.streamedText : message.content;
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  function downloadMarkdown() {
    const text = isLiveAssistant ? liveProjection!.streamedText : message.content;
    const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${message.run_id || "sb-agent-answer"}.md`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  return (
    <Message className="conversation-message mb-9" from={isUser ? "user" : "assistant"}>
      {!isUser ? (
        <div className="mb-2 flex min-h-8 items-center gap-2.5 text-xs font-semibold text-muted-foreground">
          <AgentMark />
          <span className="text-foreground">SB Agent</span>
          {answerActive ? (
            <span className="answer-activity" role="status">
              <span aria-hidden="true" className="answer-activity-dot" />
              {answerText ? "正在书写" : "正在处理"}
            </span>
          ) : null}
          {(!isLiveAssistant || answerSettled) ? <MessageTimestamp createdAt={message.created_at} /> : null}
        </div>
      ) : null}
      {!isUser && (isStreamOrigin || message.run_id) ? (
        <AssistantExecutionSection
          answerStreaming={Boolean(showStreamingAnswer && answerActive)}
          liveProjection={liveProjection}
          message={message}
        />
      ) : null}
      {showAnswer || (isUser && messageAttachments.length) ? (
        <MessageContent className="mt-1">
          {isUser && messageAttachments.length ? (
            <MessageUserAttachments attachments={messageAttachments} />
          ) : null}
          {isUser && message.content && !isAttachmentOnlyPlaceholder(message.content) ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : null}
          {!isUser && showAnswer ? (
            <Suspense fallback={<MarkdownFallback />}>
              <HistoricalCitationMessage
                answerSettled={answerSettled || !answerActive}
                citationBundle={liveProjection?.citationBundle ?? null}
                isStreamOrigin={isStreamOrigin}
                message={message}
                text={answerText}
              />
            </Suspense>
          ) : null}
        </MessageContent>
      ) : answerActive && !showStreamingAnswer && !liveProjection?.timelineItems.length ? (
        <MessageContent className="mt-1">
          <div className="answer-placeholder" aria-label="正在准备回答" role="status">
            <span />
            <span />
            <span />
          </div>
        </MessageContent>
      ) : null}
      {!isUser ? <MessageArtifacts artifacts={artifacts} /> : null}
      {!isUser && isLiveAssistant ? (
        <RunInteractionCard
          busy={isRunning}
          errorMessage={errorMessage}
          onCancel={onCancelConfirmation}
          onConfirm={onConfirm}
          onDismissError={onDismissError}
          {...(onDatabaseConnect ? { onDatabaseConnect } : {})}
          onRetry={onRetry}
          projection={liveProjection}
        />
      ) : null}
      {!isUser && showAnswer && !answerActive ? (
        <p className="text-xs text-muted-foreground/70">
          以上内容由 SB Agent 生成，仅供参考。
        </p>
      ) : null}
      {!answerActive ? <MessageActions className={isUser ? "" : "conversation-actions"}>
        <MessageAction
          disabled={isLiveAssistant && !liveProjection?.responseCompleted}
          label={copied ? "已复制" : "复制消息"}
          onClick={() => void copyMessage()}
        >
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        </MessageAction>
        {!isUser ? (
          <MessageAction
            disabled={isLiveAssistant && !liveProjection?.responseCompleted}
            label="下载 Markdown"
            onClick={downloadMarkdown}
          >
            <Download aria-hidden="true" />
          </MessageAction>
        ) : null}
        {!isUser && isLastAssistant && canRegenerate && onRegenerate ? (
          <MessageAction
            disabled={isRunning}
            label="重新生成回答"
            onClick={onRegenerate}
          >
            <RotateCcw aria-hidden="true" />
          </MessageAction>
        ) : null}
      </MessageActions> : null}
      {isUser ? (
        <div className="flex min-h-4 items-center justify-end pr-0.5 text-[11px] text-muted-foreground/70">
          <MessageTimestamp createdAt={message.created_at} showSeparator={false} />
        </div>
      ) : null}
    </Message>
  );
}

function AssistantExecutionSection({
  answerStreaming,
  liveProjection,
  message,
}: {
  answerStreaming: boolean;
  liveProjection: RunProjection | null;
  message: ConversationMessage;
}) {
  if (liveProjection?.origin === "stream") {
    return (
      <ExecutionTimeline
        active={liveProjection.status === "running" || liveProjection.status === "responding"}
        answerStreaming={answerStreaming}
        items={liveProjection.timelineItems}
        startedAt={liveProjection.startedAt}
        updatedAt={liveProjection.updatedAt}
      />
    );
  }
  return (
    <MessageRunTimeline message={message} />
  );
}

function HistoricalCitationMessage({
  answerSettled,
  citationBundle,
  isStreamOrigin,
  message,
  text,
}: {
  answerSettled: boolean;
  citationBundle: RunProjection["citationBundle"];
  isStreamOrigin: boolean;
  message: ConversationMessage;
  text: string;
}) {
  const { user } = useAuth();
  const userId = user!.id;
  const runId = message.run_id ?? "";
  const containerRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const isStreaming = isStreamOrigin && !answerSettled;
  useEffect(() => {
    if (visible || isStreaming) {
      return;
    }
    const element = containerRef.current;
    if (!element || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "320px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [isStreaming, visible]);
  const runDetailQuery = useQuery({
    queryKey: ["run", userId, message.session_id, runId],
    queryFn: () => getRun(userId, runId),
    enabled: Boolean(runId) && visible && !isStreamOrigin && !citationBundle,
    retry: false,
    staleTime: Infinity,
  });
  return (
    <div ref={containerRef}>
      <CitationMessageResponse
        citationBundle={citationBundle ?? runDetailQuery.data?.citation_bundle ?? null}
        settled={isStreamOrigin && answerSettled}
        streaming={isStreamOrigin}
      >
        {text}
      </CitationMessageResponse>
    </div>
  );
}

function MessageRunTimeline({
  message,
}: {
  message: ConversationMessage;
}) {
  const { user } = useAuth();
  const userId = user!.id;
  const [requested, setRequested] = useState(false);
  const runId = message.run_id ?? "";
  const runDetailQuery = useQuery({
    queryKey: ["run", userId, message.session_id, runId],
    queryFn: () => getRun(userId, runId),
    enabled: requested,
    retry: false,
    staleTime: Infinity,
  });

  if (!requested) {
    return (
      <button
        aria-label="思考过程：查看该轮执行过程"
        className="mb-3 flex items-center gap-1.5 rounded-sm py-1 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => setRequested(true)}
        type="button"
      >
        <span>已思考</span>
        <span className="text-muted-foreground/70">· 查看过程</span>
        <ChevronDown
          aria-hidden="true"
          className="size-3.5 shrink-0 opacity-60"
        />
      </button>
    );
  }

  if (runDetailQuery.isLoading) {
    return (
      <p className="mb-3 flex items-center gap-1.5 py-1 text-sm text-muted-foreground" role="status">
        <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" />
        正在加载思考过程…
      </p>
    );
  }

  if (runDetailQuery.isError) {
    const unavailable = runDetailQuery.error instanceof ApiError
      && runDetailQuery.error.status === 404;
    if (unavailable) {
      return (
        <p className="mb-3 rounded-xl border border-border/80 bg-card px-3 py-2.5 text-sm text-muted-foreground">
          该轮执行过程已不可用。
        </p>
      );
    }
    return (
      <div className="mb-3 flex min-h-11 items-center gap-3 rounded-xl border border-border/80 bg-card px-3">
        <p className="min-w-0 flex-1 text-sm text-muted-foreground" role="alert">
          执行过程加载失败。
        </p>
        <Button
          onClick={() => void runDetailQuery.refetch()}
          size="sm"
          variant="outline"
        >
          重试
        </Button>
      </div>
    );
  }

  if (!runDetailQuery.data) {
    return null;
  }
  const runProjection = historicalRunProjection(runDetailQuery.data);
  return (
    <ExecutionTimeline
      active={false}
      items={runProjection.timelineItems}
      startedAt={runProjection.startedAt}
      updatedAt={runProjection.updatedAt}
    />
  );
}

function buildDisplayMessages(
  messages: ConversationMessage[],
  projection: RunProjection | null,
): ConversationMessage[] {
  if (!projection?.runId) {
    return messages;
  }
  const hasPersistedAssistant = messages.some(
    (message) => message.role === "assistant" && message.run_id === projection.runId,
  );
  if (hasPersistedAssistant) {
    return messages;
  }
  return [
    ...messages,
    {
      id: LIVE_ASSISTANT_MESSAGE_ID,
      session_id: messages.at(-1)?.session_id ?? "",
      role: "assistant",
      content: projection.streamedText,
      created_at: projection.startedAt,
      run_id: projection.runId,
    },
  ];
}

function resolveLiveProjection(
  message: ConversationMessage,
  projection: RunProjection | null,
  isRunning: boolean,
): RunProjection | null {
  if (!projection?.runId || message.role !== "assistant") {
    return null;
  }
  if (message.run_id !== projection.runId) {
    return null;
  }
  if (projection.origin === "stream") {
    return projection;
  }
  const hasPersistedContent = message.id !== LIVE_ASSISTANT_MESSAGE_ID
    && Boolean(message.content.trim());
  if (hasPersistedContent && !isRunning) {
    return null;
  }
  return projection;
}

function hasVisibleRunInteraction(
  projection: RunProjection,
  errorMessage: string | null,
): boolean {
  return Boolean(
    errorMessage
    || [
      "waiting_confirmation",
      "waiting_input",
      "failed",
      "cancel_requested",
      "cancelled",
    ].includes(projection.status),
  );
}

function MessageTimestamp({
  createdAt,
  showSeparator = true,
}: {
  createdAt: string | null;
  showSeparator?: boolean;
}) {
  const formatted = renderMessageTime(createdAt);
  if (!formatted) {
    return null;
  }
  return (
    <>
      {showSeparator ? <span aria-hidden="true">·</span> : null}
      <time dateTime={createdAt ?? undefined}>{formatted}</time>
    </>
  );
}

function renderMessageTime(createdAt: string | null): string {
  if (!createdAt) {
    return "";
  }
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const now = new Date();
  const sameDay = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  const sameYear = date.getFullYear() === now.getFullYear();
  return new Intl.DateTimeFormat("zh-CN", {
    year: sameDay ? undefined : sameYear ? undefined : "numeric",
    month: sameDay ? undefined : "2-digit",
    day: sameDay ? undefined : "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function MarkdownFallback() {
  return (
    <div aria-label="正在准备回答排版" className="space-y-2" role="status">
      <div className="h-4 w-full animate-pulse rounded bg-muted" />
      <div className="h-4 w-4/5 animate-pulse rounded bg-muted" />
    </div>
  );
}

function MessageSkeleton() {
  return (
    <div aria-label="正在加载消息" className="mx-auto max-w-[860px] space-y-5 px-6 py-8" role="status">
      <div className="h-20 w-2/3 animate-pulse rounded-2xl bg-muted" />
      <div className="h-28 animate-pulse rounded-xl bg-muted" />
    </div>
  );
}


function isAttachmentOnlyPlaceholder(content: string): boolean {
  return content.trim() === "请结合附件内容回答。";
}

function messageIdentity(message: ConversationMessage): string {
  return message.run_id
    ? `run:${message.run_id}:${message.role}`
    : `message:${message.id}`;
}
