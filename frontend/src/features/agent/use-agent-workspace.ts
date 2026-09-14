import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { historicalRunProjection } from "@/features/runs/run-format";
import { useAuth } from "@/features/auth/auth-provider";
import {
  cancelRun,
  getRun,
  getSession,
  listRunActivity,
  listSessionArtifacts,
  listSessionAttachments,
  listSessionRuns,
  listSessions,
  uploadSessionAttachment,
} from "@/lib/api/agent";
import {
  saveDatabaseConnection,
  type DatabaseConnectionWritePayload,
} from "@/lib/api/management";
import type {
  AskRequest,
  Attachment,
  ConversationMessage,
  RunUsage,
  RunSummary,
  SessionSummary,
} from "@/lib/api/schemas";
import {
  createRunProjection,
  type RunProjection,
} from "@/lib/stream/run-projection";
import {
  isTerminalProjection,
  recoverAgentRun,
  streamAgentRun,
} from "@/lib/stream/client";

import {
  createAttachmentPreviewUrl,
  releaseAttachmentPreviewUrl,
} from "./attachment-preview";
import { sessionDateRange, type SessionTimeFilter } from "./session-time-filter";

const ACTIVE_RUN_STORAGE_PREFIX = "sb-agent-active-run:v2:";
const COMPLETED_SESSIONS_STORAGE_PREFIX = "sb-agent-completed-sessions:v2:";
let optimisticMessageId = -1;

export interface PendingAttachment {
  localId: string;
  file: File;
  previewUrl: string;
  attachment?: Attachment;
  uploading: boolean;
  error?: string;
}

export interface QueuedMessage {
  id: string;
  query: string;
  attachments: PendingAttachment[];
}

interface SendOptions {
  appendUserMessage?: boolean;
  suppressUserMessage?: boolean;
  attachmentIdsOverride?: string[];
  pendingAttachmentsOverride?: PendingAttachment[];
}

interface LocalSessionEntry {
  session: SessionSummary;
  running: boolean;
}

const SESSION_PAGE_SIZE = 30;

export function useAgentWorkspace(sessionId: string, sessionKeyword = "") {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const userId = user!.id;
  const [projection, setProjection] = useState<RunProjection | null>(null);
  const [optimisticMessages, setOptimisticMessages] = useState<ConversationMessage[]>([]);
  const [optimisticTitle, setOptimisticTitle] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [queuedMessages, setQueuedMessages] = useState<Map<string, QueuedMessage>>(new Map());
  const [timeFilter, setTimeFilter] = useState<SessionTimeFilter>("all");
  const [isRunning, setIsRunning] = useState(false);
  const [localActiveSessions, setLocalActiveSessions] = useState<Map<string, LocalSessionEntry>>(new Map());
  const [completedSessionIds, setCompletedSessionIds] = useState<string[]>(
    () => readCompletedSessionIds(userId),
  );
  useEffect(() => setCompletedSessionIds(readCompletedSessionIds(userId)), [userId]);
  const [completionNotice, setCompletionNotice] = useState<{
    sessionId: string;
    title: string;
  } | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [dismissedErrorRunId, setDismissedErrorRunId] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const pendingProjectionRef = useRef<RunProjection | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const lastQueryRef = useRef("");
  const resumeAttemptedRef = useRef("");
  const previousActiveRunStatusesRef = useRef<Map<string, string>>(new Map());
  const baselineEstablishedRef = useRef(false);
  const queuedDispatchRef = useRef("");
  const queuedMessage = queuedMessages.get(sessionId) ?? null;

  const normalizedSessionKeyword = sessionKeyword.trim();
  const sessionRange = useMemo(() => sessionDateRange(timeFilter), [timeFilter]);
  const sessionsQuery = useInfiniteQuery({
    queryKey: ["sessions", userId, normalizedSessionKeyword, timeFilter],
    queryFn: ({ pageParam }) => listSessions(userId, {
      keyword: normalizedSessionKeyword,
      ...(sessionRange.dateFrom ? { dateFrom: sessionRange.dateFrom } : {}),
      ...(sessionRange.dateTo ? { dateTo: sessionRange.dateTo } : {}),
      limit: SESSION_PAGE_SIZE,
      offset: pageParam,
    }),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => {
      const nextOffset = lastPage.offset + lastPage.items.length;
      return nextOffset < lastPage.total ? nextOffset : undefined;
    },
  });
  const detailQuery = useQuery({
    queryKey: ["session", userId, sessionId],
    queryFn: () => getSession(userId, sessionId),
    enabled: Boolean(sessionId),
  });
  const sessionDetail = detailQuery.data?.session_id === sessionId
    ? detailQuery.data
    : undefined;
  const attachmentsQuery = useQuery({
    queryKey: ["attachments", userId, sessionId],
    queryFn: () => listSessionAttachments(userId, sessionId),
    enabled: Boolean(sessionId),
  });
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", userId, sessionId],
    queryFn: () => listSessionArtifacts(userId, sessionId),
    enabled: Boolean(sessionId),
    retry: false,
  });
  const latestRunQuery = useQuery({
    queryKey: ["session-latest-run", userId, sessionId],
    queryFn: () => listSessionRuns(userId, sessionId),
    enabled: Boolean(sessionId),
    retry: false,
  });
  const latestRun = latestRunQuery.data?.items[0];
  const historicalRunId = latestRun
    && !["running", "queued", "cancel_requested"].includes(latestRun.status)
    && latestRun.has_detail
    ? latestRun.run_id
    : "";
  const latestRunDetailQuery = useQuery({
    queryKey: ["run", userId, sessionId, historicalRunId],
    queryFn: () => getRun(userId, historicalRunId),
    enabled: Boolean(sessionId && historicalRunId),
    retry: false,
  });
  const activeRunsQuery = useQuery({
    queryKey: ["active-runs", userId],
    queryFn: () => listRunActivity(userId),
    refetchInterval: (query) => {
      const hasActiveRun = (query.state.data?.items ?? []).some(
        (run) => isActiveRunStatus(run.status),
      );
      return hasActiveRun ? 2000 : 5000;
    },
    retry: false,
  });

  const publishProjection = useCallback((next: RunProjection) => {
    pendingProjectionRef.current = next;
    const runId = next.runId;
    if (runId) {
      window.localStorage.setItem(activeRunStorageKey(userId, sessionId), runId);
      // 把 run_id 绑到本轮乐观用户消息上，后台落库后 React key 仍是 run:{id}:user，
      // 避免回复结束时用户气泡卸载重挂、滚动区跟着跳回顶部。
      setOptimisticMessages((current) => bindOptimisticUserRunId(current, sessionId, runId));
    }
    if (isTerminalProjection(next)) {
      if (animationFrameRef.current !== null) {
        window.cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
      pendingProjectionRef.current = null;
      setProjection(next);
      return;
    }
    if (animationFrameRef.current === null) {
      animationFrameRef.current = window.requestAnimationFrame(() => {
        animationFrameRef.current = null;
        const pending = pendingProjectionRef.current;
        pendingProjectionRef.current = null;
        if (pending) {
          setProjection(pending);
        }
      });
    }
  }, [sessionId, userId]);

  const upsertLocalActiveSession = useCallback((entry: SessionSummary) => {
    setLocalActiveSessions((current) => {
      const existing = current.get(entry.session_id);
      if (existing?.running) {
        return current;
      }
      const next = new Map(current);
      next.set(entry.session_id, { session: existing?.session ?? entry, running: true });
      return next;
    });
  }, []);

  const settleLocalSession = useCallback((targetSessionId: string) => {
    setLocalActiveSessions((current) => {
      const existing = current.get(targetSessionId);
      if (!existing?.running) {
        return current;
      }
      const next = new Map(current);
      next.set(targetSessionId, { ...existing, running: false });
      return next;
    });
  }, []);

  const refreshConversation = useCallback(async () => {
    const [detailResult] = await Promise.all([
      detailQuery.refetch(),
      artifactsQuery.refetch(),
      queryClient.invalidateQueries({ queryKey: ["sessions", userId] }),
      queryClient.invalidateQueries({
        queryKey: ["session-latest-run", userId, sessionId],
      }),
    ]);
    if (detailResult.data) {
      const hasPersistedUserMessage = detailResult.data.messages.some(
        (message) => message.role === "user",
      );
      if (hasPersistedUserMessage) {
        setOptimisticTitle(null);
      }
      setOptimisticMessages((current) => current.filter(
        (optimistic) => !detailResult.data.messages.some(
          (persisted) => persisted.role === optimistic.role
            && persisted.content === optimistic.content,
        ),
      ));
    }
  }, [artifactsQuery, detailQuery, queryClient, sessionId, userId]);

  const clearPendingAttachments = useCallback((items: PendingAttachment[]) => {
    items.forEach((item) => releaseAttachmentPreviewUrl(item.previewUrl));
  }, []);

  const executeQuery = useCallback(async (
    query: string,
    options: SendOptions = {},
  ) => {
    const requestAttachments = options.pendingAttachmentsOverride ?? pendingAttachments;
    const attachmentIds = options.attachmentIdsOverride ?? requestAttachments
      .filter((item) => item.attachment?.id && !item.error)
      .map((item) => item.attachment!.id);
    const normalizedQuery = query.trim() || (attachmentIds.length ? "请结合附件内容回答。" : "");
    if ((!normalizedQuery && !attachmentIds.length) || isRunning) {
      return;
    }
    if (requestAttachments.some((item) => item.uploading)) {
      return;
    }
    const requestSessionId = sessionId;
    const appendUserMessage = options.appendUserMessage ?? true;
    const hasExistingMessages = Boolean(
      (sessionDetail?.messages.length ?? 0)
        || optimisticMessages.some((message) => message.session_id === sessionId),
    );
    const history = [
      ...(sessionDetail?.messages ?? []),
      ...optimisticMessages.filter((message) => message.session_id === sessionId),
    ];
    const request: AskRequest = {
      query: normalizedQuery,
      user_id: userId,
      session_id: sessionId,
      client_messages: history
        .filter((message) => ["user", "assistant", "system"].includes(message.role))
        .slice(-12)
        .map((message) => ({
          role: message.role as "user" | "assistant" | "system",
          content: message.content,
          created_at: message.created_at,
        })),
      attachment_ids: attachmentIds,
      suppress_user_message: options.suppressUserMessage ?? false,
    };

    const sentAttachments = requestAttachments
      .filter((item) => item.attachment?.id && attachmentIds.includes(item.attachment.id))
      .map((item) => ({
        ...item.attachment!,
        preview_url: item.previewUrl,
      }));
    if (appendUserMessage) {
      setOptimisticMessages((current) => [
        ...current,
        {
          id: optimisticMessageId--,
          session_id: sessionId,
          role: "user",
          content: redactVisibleDatabaseSecret(normalizedQuery),
          created_at: new Date().toISOString(),
          run_id: null,
          attachment_ids: attachmentIds,
          attachments: sentAttachments,
        },
      ]);
    }
    // 新会话首次发送时立即生成乐观标题，让页头和会话侧栏无需等待 Agent 完成；
    // 只在首次有效用户文本时写入一次，后续发送、确认、重试都不会覆盖。
    if (appendUserMessage && !hasExistingMessages && optimisticTitle === null) {
      setOptimisticTitle(summarizeOptimisticSessionTitle(normalizedQuery));
    }
    lastQueryRef.current = normalizedQuery;
    setErrorMessage(null);
    setDismissedErrorRunId(null);
    setProjection(createRunProjection());
    // 消息一旦发出就清空待发附件，避免等待回复期间缩略图仍占着输入框。
    if (options.pendingAttachmentsOverride) {
      clearPendingAttachments(options.pendingAttachmentsOverride);
    } else {
      setPendingAttachments((current) => {
        clearPendingAttachments(current);
        return [];
      });
    }
    setIsRunning(true);
    upsertLocalActiveSession({
      session_id: requestSessionId,
      title: summarizeOptimisticSessionTitle(normalizedQuery),
      preview: redactVisibleDatabaseSecret(normalizedQuery),
      message_count: 1,
      last_message_at: new Date().toISOString(),
      has_summary: false,
    });
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const finalProjection = await streamAgentRun(request, {
        signal: controller.signal,
        onProjection: publishProjection,
      });
      publishProjection(finalProjection);
      // 终态先留在本地消息里：后台同步期间继续提问也不会丢失上一条回答和上下文。
      if (finalProjection.streamedText) {
        setOptimisticMessages((current) => [...current, {
          id: optimisticMessageId--,
          session_id: requestSessionId,
          role: "assistant",
          content: finalProjection.streamedText,
          created_at: new Date().toISOString(),
          run_id: finalProjection.runId,
        }]);
      }
      if (finalProjection.error) {
        setErrorMessage(finalProjection.error.message);
      }
      window.localStorage.removeItem(activeRunStorageKey(userId, requestSessionId));
      settleLocalSession(requestSessionId);
      void refreshConversation().catch(() => {
        // 回复已完成，后台同步失败不应把成功回答改成失败或锁住输入框。
      });
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setErrorMessage(error instanceof Error ? error.message : "Agent 请求失败。");
      }
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
        setIsRunning(false);
      }
    }
  }, [
    isRunning,
    optimisticMessages,
    optimisticTitle,
    publishProjection,
    settleLocalSession,
    refreshConversation,
    clearPendingAttachments,
    pendingAttachments,
    sessionDetail?.messages,
    sessionId,
    upsertLocalActiveSession,
    userId,
  ]);

  const sendQuery = useCallback(async (query: string) => {
    if (!isRunning) {
      await executeQuery(query);
      return;
    }
    if (queuedMessage || pendingAttachments.some((item) => item.uploading)) {
      return;
    }
    const nextQueuedMessage: QueuedMessage = {
      id: crypto.randomUUID(),
      query: query.trim(),
      attachments: pendingAttachments,
    };
    setQueuedMessages((current) => {
      const next = new Map(current);
      next.set(sessionId, nextQueuedMessage);
      return next;
    });
    setPendingAttachments([]);
  }, [executeQuery, isRunning, pendingAttachments, queuedMessage, sessionId]);

  const editQueuedMessage = useCallback(() => {
    if (!queuedMessage) {
      return;
    }
    setQueuedMessages((current) => {
      const next = new Map(current);
      next.delete(sessionId);
      return next;
    });
    setPendingAttachments((current) => {
      clearPendingAttachments(current);
      return queuedMessage.attachments;
    });
  }, [clearPendingAttachments, queuedMessage, sessionId]);

  const cancelQueuedMessage = useCallback(() => {
    if (!queuedMessage) {
      return;
    }
    clearPendingAttachments(queuedMessage.attachments);
    setQueuedMessages((current) => {
      const next = new Map(current);
      next.delete(sessionId);
      return next;
    });
  }, [clearPendingAttachments, queuedMessage, sessionId]);

  useEffect(() => {
    if (
      isRunning
      || !queuedMessage
      || queuedDispatchRef.current === queuedMessage.id
      || !projection
      || !["completed", "failed", "cancelled"].includes(projection.status)
    ) {
      return;
    }
    queuedDispatchRef.current = queuedMessage.id;
    setQueuedMessages((current) => {
      const next = new Map(current);
      next.delete(sessionId);
      return next;
    });
    void executeQuery(queuedMessage.query, {
      pendingAttachmentsOverride: queuedMessage.attachments,
    });
  }, [executeQuery, isRunning, projection, queuedMessage, sessionId]);

  // 切换会话时必须在绘制前清空本地状态，否则新建对话会短暂带上上一会话的乐观消息。
  useLayoutEffect(() => {
    setProjection(null);
    setOptimisticMessages([]);
    setOptimisticTitle(null);
    setPendingAttachments((current) => {
      clearPendingAttachments(current);
      return [];
    });
    setErrorMessage(null);
    setDismissedErrorRunId(null);
    resumeAttemptedRef.current = "";
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setIsRunning(false);
  }, [clearPendingAttachments, sessionId, userId]);

  // 用户进入/选择某会话即视为已查看：立即清除该会话的未读完成圆点并持久化。
  useEffect(() => {
    setCompletedSessionIds((current) => {
      if (!current.includes(sessionId)) {
        return current;
      }
      return current.filter((id) => id !== sessionId);
    });
    setCompletionNotice((current) => (
      current?.sessionId === sessionId ? null : current
    ));
  }, [sessionId]);

  // 未读完成集合变化时持久化到 localStorage，刷新页面后仍保留。
  useEffect(() => {
    writeCompletedSessionIds(userId, completedSessionIds);
  }, [completedSessionIds, userId]);

  useEffect(() => () => {
    abortControllerRef.current?.abort();
    if (animationFrameRef.current !== null) {
      window.cancelAnimationFrame(animationFrameRef.current);
    }
  }, []);

  useEffect(() => {
    if (!sessionId || isRunning || latestRunQuery.isLoading) {
      return;
    }
    const storedRunId = window.localStorage.getItem(activeRunStorageKey(userId, sessionId));
    const latestRun = latestRunQuery.data?.items[0];
    if (storedRunId && latestRun?.run_id === storedRunId
      && ["success", "error", "max_iterations", "cancelled", "needs_input", "needs_confirmation"].includes(latestRun.status)) {
      window.localStorage.removeItem(activeRunStorageKey(userId, sessionId));
      settleLocalSession(sessionId);
      return;
    }
    const recoverableLatestRun = latestRun
      && ["running", "queued", "cancel_requested"].includes(latestRun.status)
      ? latestRun.run_id
      : "";
    const runId = storedRunId || recoverableLatestRun;
    if (!runId || resumeAttemptedRef.current === `${sessionId}:${runId}`) {
      return;
    }
    resumeAttemptedRef.current = `${sessionId}:${runId}`;
    setIsRunning(true);
    setErrorMessage(null);
    upsertLocalActiveSession(latestRun
      ? synthesizeActiveSession(latestRun)
      : {
        session_id: sessionId,
        title: OPTIMISTIC_TITLE_FALLBACK,
        preview: "",
        message_count: 1,
        last_message_at: new Date().toISOString(),
        has_summary: false,
      });
    const controller = new AbortController();
    abortControllerRef.current = controller;
    void recoverAgentRun(
      userId,
      runId,
      createRunProjection({ runId }),
      { signal: controller.signal, onProjection: publishProjection },
    ).then(async (finalProjection) => {
      publishProjection(finalProjection);
      window.localStorage.removeItem(activeRunStorageKey(userId, sessionId));
      settleLocalSession(sessionId);
      void refreshConversation().catch(() => {
        // 回复已完成，后台同步失败不应把成功回答改成失败或锁住输入框。
      });
    }).catch((error: unknown) => {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setErrorMessage(error instanceof Error ? error.message : "运行恢复失败。");
      }
    }).finally(() => {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
        setIsRunning(false);
      }
    });
  }, [
    isRunning,
    latestRunQuery.data?.items,
    latestRunQuery.isLoading,
    publishProjection,
    settleLocalSession,
    refreshConversation,
    sessionId,
    upsertLocalActiveSession,
    userId,
  ]);

  useEffect(() => {
    const runDetail = latestRunDetailQuery.data;
    if (
      !sessionId
      || isRunning
      || latestRunQuery.isFetching
      || latestRunDetailQuery.isLoading
      || !runDetail
      || runDetail.session_id !== sessionId
    ) {
      return;
    }
    const storedRunId = window.localStorage.getItem(activeRunStorageKey(userId, sessionId));
    if (storedRunId) {
      return;
    }
    setProjection((current) => {
      const historical = historicalRunProjection(runDetail);
      // 历史查询可能仍指向上一轮；当前会话已收到的流式结果不能被后台同步覆盖。
      // 切换会话时上方的 useLayoutEffect 会清空 current，历史恢复仍按原流程执行。
      if (current?.origin === "stream" && current.runId) {
        return current;
      }
      return historical;
    });
  }, [
    isRunning,
    latestRunQuery.isFetching,
    latestRunDetailQuery.data,
    latestRunDetailQuery.isLoading,
    sessionId,
    userId,
  ]);

  const uploadFiles = useCallback(async (files: File[]) => {
    if (!files.length) {
      return;
    }
    const availableSlots = Math.max(0, 10 - pendingAttachments.length);
    const candidateFiles = files.slice(0, availableSlots);
    if (!candidateFiles.length) {
      setErrorMessage("单条消息最多附带 10 个附件。");
      return;
    }
    setErrorMessage(null);
    setIsUploading(true);
    let staged: PendingAttachment[] = [];
    try {
      staged = await Promise.all(candidateFiles.map(async (file) => ({
        localId: crypto.randomUUID(),
        file,
        previewUrl: await createAttachmentPreviewUrl(file),
        uploading: true,
      })));
      setPendingAttachments((current) => [...current, ...staged]);
      for (const item of staged) {
        try {
          const attachment = await uploadSessionAttachment(userId, sessionId, item.file);
          setPendingAttachments((current) => current.map((entry) => (
            entry.localId === item.localId
              ? { ...entry, attachment, uploading: false }
              : entry
          )));
          void attachmentsQuery.refetch();
        } catch (error) {
          const message = error instanceof Error ? error.message : "附件上传失败。";
          setPendingAttachments((current) => current.map((entry) => (
            entry.localId === item.localId
              ? { ...entry, uploading: false, error: message }
              : entry
          )));
          setErrorMessage(message);
        }
      }
    } finally {
      setIsUploading(false);
    }
  }, [attachmentsQuery, pendingAttachments.length, sessionId, userId]);

  const removePendingAttachment = useCallback((localId: string) => {
    setPendingAttachments((current) => {
      const target = current.find((item) => item.localId === localId);
      if (target) {
        releaseAttachmentPreviewUrl(target.previewUrl);
      }
      return current.filter((item) => item.localId !== localId);
    });
  }, []);

  const connectDatabase = useCallback(async (
    payload: DatabaseConnectionWritePayload,
    requestId: string,
  ) => {
    const saved = await saveDatabaseConnection({
      ...payload,
      interaction_request_id: requestId,
    });
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["database-connections"] }),
      queryClient.invalidateQueries({ queryKey: ["tools"] }),
    ]);
    if (saved.continuation_query) {
      await executeQuery(saved.continuation_query, {
        appendUserMessage: false,
        suppressUserMessage: true,
      });
    }
  }, [executeQuery, queryClient]);

  const stopRun = useCallback(async () => {
    if (!projection?.runId || projection.status === "cancel_requested") {
      return;
    }
    try {
      await cancelRun(userId, projection.runId);
      setProjection((current) => current
        ? { ...current, status: "cancel_requested" }
        : current);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "停止请求提交失败。");
    }
  }, [projection?.runId, projection?.status, userId]);

  const markSessionRead = useCallback((targetSessionId: string) => {
    setCompletedSessionIds((current) => {
      if (!current.includes(targetSessionId)) {
        return current;
      }
      return current.filter((id) => id !== targetSessionId);
    });
    setCompletionNotice((current) => (
      current?.sessionId === targetSessionId ? null : current
    ));
  }, []);

  const dismissCompletionNotice = useCallback(() => {
    setCompletionNotice(null);
  }, []);

  const dismissRunError = useCallback(() => {
    setErrorMessage(null);
    if (projection?.status !== "failed") {
      return;
    }
    if (projection.runId) {
      setDismissedErrorRunId(projection.runId);
      return;
    }
    setProjection(null);
  }, [projection?.runId, projection?.status]);

  const messages = useMemo(
    () => [
      ...(sessionDetail?.messages ?? []),
      ...optimisticMessages.filter((message) => message.session_id === sessionId
        && !(message.run_id && (sessionDetail?.messages ?? []).some((persisted) => (
          persisted.role === message.role
          && persisted.run_id === message.run_id
        )))),
    ],
    [optimisticMessages, sessionDetail?.messages, sessionId],
  );
  const lastUserMessage = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message && message.role === "user" && message.content.trim()) {
        return message.content;
      }
    }
    return "";
  }, [messages]);
  const regenerateLastAnswer = useCallback(() => {
    if (isRunning || !lastUserMessage) {
      return;
    }
    void executeQuery(lastUserMessage, {
      appendUserMessage: false,
      suppressUserMessage: true,
      attachmentIdsOverride: [],
    });
  }, [executeQuery, isRunning, lastUserMessage]);
  const sessions = useMemo(() => {
    const seen = new Set<string>();
    const serverSessions = (sessionsQuery.data?.pages ?? [])
      .flatMap((page) => page.items)
      .filter((session) => {
        if (seen.has(session.session_id)) {
          return false;
        }
        seen.add(session.session_id);
        return true;
      });
    const activeSessions: SessionSummary[] = [];
    for (const run of activeRunsQuery.data?.items ?? []) {
      if (!isActiveRunStatus(run.status) || seen.has(run.session_id)) {
        continue;
      }
      const candidate = synthesizeActiveSession(run);
      if (!matchesSessionKeyword(candidate, normalizedSessionKeyword)
        || !isSessionWithinDateRange(candidate.last_message_at, sessionRange)) {
        continue;
      }
      activeSessions.push(candidate);
      seen.add(candidate.session_id);
    }
    const localSessions: SessionSummary[] = [];
    for (const entry of localActiveSessions.values()) {
      const candidate = entry.session;
      if (seen.has(candidate.session_id)
        || !matchesSessionKeyword(candidate, normalizedSessionKeyword)
        || !isSessionWithinDateRange(candidate.last_message_at, sessionRange)) {
        continue;
      }
      localSessions.push(candidate);
      seen.add(candidate.session_id);
    }
    return [...activeSessions, ...localSessions, ...serverSessions];
  }, [
    activeRunsQuery.data?.items,
    localActiveSessions,
    normalizedSessionKeyword,
    sessionRange,
    sessionsQuery.data?.pages,
  ]);
  const activeRunSessionIds = useMemo(() => {
    const ids = new Set<string>();
    for (const run of activeRunsQuery.data?.items ?? []) {
      if (isActiveRunStatus(run.status)) {
        ids.add(run.session_id);
      }
    }
    return ids;
  }, [activeRunsQuery.data?.items]);
  const activeRunStatusBySession = useMemo(() => {
    const statusBySession = new Map<string, string>();
    for (const run of activeRunsQuery.data?.items ?? []) {
      if (!statusBySession.has(run.session_id)) {
        statusBySession.set(run.session_id, run.status);
      }
    }
    return statusBySession;
  }, [activeRunsQuery.data?.items]);
  const activeSessionIds = useMemo(() => {
    const ids = new Set(activeRunSessionIds);
    for (const [id, entry] of localActiveSessions) {
      if (entry.running) {
        ids.add(id);
      }
    }
    return ids;
  }, [activeRunSessionIds, localActiveSessions]);
  // 当前会话的上下文观察（含注入预算与分类估算），无数据库/无记录时为 null。
  const contextObservation = detailQuery.data?.context_observation ?? null;
  // 最近一次有详情运行的模型 usage：优先用流式终态里的实时上报（延迟更低），
  // 刷新详情后若终态缺失再回退到 Run API 的历史数据。
  const latestRunUsage = useMemo(() => {
    const terminal = projection?.terminalResult?.usage;
    const terminalUsage = normalizeRunUsage(terminal);
    if (terminalUsage) {
      return terminalUsage;
    }
    return latestRunDetailQuery.data?.usage ?? null;
  }, [latestRunDetailQuery.data?.usage, projection?.terminalResult]);
  // 未读完成检测：只认「本页面观察到活跃（queued/running/cancel_requested）
  // → 本次成功数据明确为 success」的转换；第一次成功轮询只建立基线，
  // 避免把历史 success 会话全部标成未读；轮询错误不会改动状态。
  useEffect(() => {
    if (
      activeRunsQuery.isLoading
      || activeRunsQuery.isFetching
      || !activeRunsQuery.isSuccess
    ) {
      return;
    }
    const runs = activeRunsQuery.data?.items ?? [];
    const currentStatusBySession = new Map<string, string>();
    for (const run of runs) {
      if (!currentStatusBySession.has(run.session_id)) {
        currentStatusBySession.set(run.session_id, run.status);
      }
    }
    const newlyCompleted: string[] = [];
    // 本地活跃会话由本浏览器发起/恢复，属于已经观察到的活跃状态；
    // 本次数据明确为 success 且用户不在该会话时，视为一次未读完成。
    for (const localId of localActiveSessions.keys()) {
      if (
        localId !== sessionId
        && currentStatusBySession.get(localId) === "success"
      ) {
        newlyCompleted.push(localId);
      }
    }
    if (!baselineEstablishedRef.current) {
      previousActiveRunStatusesRef.current = new Map(currentStatusBySession);
      baselineEstablishedRef.current = true;
    } else {
      const previousStatuses = previousActiveRunStatusesRef.current;
      for (const [runSessionId, previousStatus] of previousStatuses) {
        if (
          isActiveRunStatus(previousStatus)
          && currentStatusBySession.get(runSessionId) === "success"
          && runSessionId !== sessionId
          && !newlyCompleted.includes(runSessionId)
        ) {
          newlyCompleted.push(runSessionId);
        }
      }
      previousActiveRunStatusesRef.current = new Map(currentStatusBySession);
    }
    if (newlyCompleted.length === 0) {
      return;
    }
    const firstCompletedId = newlyCompleted[0];
    if (!firstCompletedId) {
      return;
    }
    const completedRun = runs.find((run) => run.session_id === firstCompletedId);
    const completedTitle = completedRun?.query_summary
      ? summarizeOptimisticSessionTitle(completedRun.query_summary)
      : localActiveSessions.get(firstCompletedId)?.session.title ?? OPTIMISTIC_TITLE_FALLBACK;
    setCompletedSessionIds((current) => {
      const nextSet = new Set(current);
      let changed = false;
      for (const id of newlyCompleted) {
        if (!nextSet.has(id)) {
          nextSet.add(id);
          changed = true;
        }
      }
      return changed ? [...nextSet] : current;
    });
    setCompletionNotice((current) => current ?? {
      sessionId: firstCompletedId,
      title: completedTitle,
    });
  }, [
    activeRunsQuery.data?.items,
    activeRunsQuery.isFetching,
    activeRunsQuery.isLoading,
    activeRunsQuery.isSuccess,
    localActiveSessions,
    sessionId,
  ]);
  useEffect(() => {
    setLocalActiveSessions((current) => {
      let changed = false;
      const next = new Map(current);
      for (const [id, entry] of current) {
        const status = activeRunStatusBySession.get(id);
        if (entry.running && status !== undefined && !isActiveRunStatus(status)) {
          next.set(id, { ...entry, running: false });
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [activeRunStatusBySession]);
  useEffect(() => {
    const serverIds = new Set(
      (sessionsQuery.data?.pages ?? []).flatMap((page) => (
        page.items.map((item) => item.session_id)
      )),
    );
    setLocalActiveSessions((current) => {
      let changed = false;
      const next = new Map(current);
      for (const [id, entry] of current) {
        if (!entry.running && serverIds.has(id)) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [sessionsQuery.data?.pages]);
  const previousActiveSessionIdsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const previous = previousActiveSessionIdsRef.current;
    for (const id of previous) {
      if (!activeSessionIds.has(id)) {
        void queryClient.invalidateQueries({ queryKey: ["sessions", userId] });
        // 当前正在看的会话已由 refreshConversation 静默同步，再 invalidate 会把消息列表重拉一遍。
        if (id === sessionId) {
          continue;
        }
        void queryClient.invalidateQueries({ queryKey: ["session", userId, id] });
        void queryClient.invalidateQueries({
          queryKey: ["session-latest-run", userId, id],
        });
        void queryClient.invalidateQueries({
          queryKey: ["artifacts", userId, id],
        });
      }
    }
    previousActiveSessionIdsRef.current = activeSessionIds;
  }, [activeSessionIds, queryClient, sessionId, userId]);

  return {
    userId,
    sessions,
    activeSessionIds: [...activeSessionIds],
    sessionsLoading: sessionsQuery.isLoading,
    sessionsError: sessionsQuery.error,
    hasNextSessionsPage: sessionsQuery.hasNextPage,
    isFetchingNextSessionsPage: sessionsQuery.isFetchingNextPage,
    loadNextSessionsPage: sessionsQuery.fetchNextPage,
    totalSessions: Math.max(sessionsQuery.data?.pages[0]?.total ?? 0, sessions.length),
    timeFilter,
    setTimeFilter,
    messages,
    messagesLoading: (
      detailQuery.isPending
      || (detailQuery.isFetching && detailQuery.data?.session_id !== sessionId)
    ) && optimisticMessages.length === 0 && !projection,
    optimisticTitle,
    detailError: detailQuery.error,
    artifacts: artifactsQuery.data?.items ?? [],
    sessionAttachments: attachmentsQuery.data?.items ?? [],
    pendingAttachments,
    queuedMessage,
    projection: projection?.status === "failed" && projection.runId === dismissedErrorRunId
      ? null
      : projection,
    isRunning,
    isUploading,
    errorMessage,
    sendQuery,
    confirmRun: () => executeQuery("确认", { appendUserMessage: false }),
    cancelConfirmation: () => executeQuery("取消", { appendUserMessage: false }),
    retryLastQuery: () => executeQuery(lastQueryRef.current || lastUserMessage),
    canRegenerateLastAnswer: Boolean(lastUserMessage),
    regenerateLastAnswer,
    contextObservation,
    latestRunUsage,
    stopRun,
    uploadFiles,
    completedSessionIds,
    completionNotice,
    markSessionRead,
    dismissCompletionNotice,
    removePendingAttachment,
    editQueuedMessage,
    cancelQueuedMessage,
    clearError: dismissRunError,
    connectDatabase,
  };
}

function bindOptimisticUserRunId(
  messages: ConversationMessage[],
  targetSessionId: string,
  runId: string,
): ConversationMessage[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.session_id !== targetSessionId || message.role !== "user") {
      continue;
    }
    if (message.run_id === runId) {
      return messages;
    }
    if (message.run_id == null) {
      const next = [...messages];
      next[index] = { ...message, run_id: runId };
      return next;
    }
    return messages;
  }
  return messages;
}

function redactVisibleDatabaseSecret(value: string): string {
  return value
    .replace(
      /(\b(?:mysql_)?password\s*[:=]\s*)(['"]?)([^\s,;'"&]+)\2/gi,
      "$1[已移除，请使用安全表单]",
    )
    .replace(
      /((?:jdbc:)?mysql:\/\/[^:\s/@]+:)[^@\s]+(@)/gi,
      "$1[已移除]$2",
    );
}

function normalizeRunUsage(value: unknown): RunUsage | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const numeric = (key: string): number | null => {
    const raw = record[key];
    return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
  };
  return {
    input_tokens: numeric("input_tokens"),
    output_tokens: numeric("output_tokens"),
    total_tokens: numeric("total_tokens"),
    cache_read_tokens: numeric("cache_read_tokens"),
    cache_miss_tokens: numeric("cache_miss_tokens"),
    cache_write_tokens: numeric("cache_write_tokens"),
    reasoning_tokens: numeric("reasoning_tokens"),
  };
}

const OPTIMISTIC_TITLE_MAX_CHARS = 36;
const OPTIMISTIC_TITLE_FALLBACK = "新对话";
const MARKDOWN_LEADING_PATTERN = /^(?:#{1,6}\s+|[-*+]\s+|\d{1,3}[.、)]\s+)/;
const CONVERSATION_PREFIX_PATTERN = /^(?:你好[，,、\s]+|您好[，,、\s]+)?(?:请\s*帮我|请\s*帮忙|请\s*麻烦你|帮我一下|帮帮忙|麻烦你|请你|请问|帮我|帮忙)?/;
// 与后端 _first_sentence 保持一致：中文句号、感叹号、问号及中英文感叹号、
// 问号直接作为句末；英文句号仅在后面是空白/换行/字符串结尾时视为句末，
// 避免数字小数点（如 1.2.3）和域名（如 example.com）中的点被误判为句界。
const SENTENCE_END_PATTERN = /[。！？!?]|\.(?=\s|$)/;
const SEMANTIC_TITLE_RULES: ReadonlyArray<readonly [RegExp, string]> = [
  [/^(?:你|您)?好(?:呀|啊|哇)?$|^(?:hi|hello|hey)$/i, "用户打招呼"],
  [/^(?:谢谢|多谢|感谢)(?:你|您)?(?:了|啦|啊)?$/i, "用户表示感谢"],
  [/^(?:再见|拜拜|回头见|bye|goodbye)$/i, "用户结束对话"],
  [/^(?:你是谁|你叫什么|介绍一下你自己)$/i, "了解助手身份"],
  [/^(?:你能做什么|你会做什么|你有哪些能力|你有什么功能)$/i, "了解助手能力"],
];

function summarizeOptimisticSessionTitle(text: string): string {
  const cleaned = cleanOptimisticTitleText(text);
  if (!cleaned) {
    return OPTIMISTIC_TITLE_FALLBACK;
  }
  for (const [pattern, title] of SEMANTIC_TITLE_RULES) {
    if (pattern.test(cleaned.trim())) {
      return title;
    }
  }
  return cleaned.slice(0, OPTIMISTIC_TITLE_MAX_CHARS);
}

// 与后端 _clean_title_text 保持一致：去代码围栏、行首 Markdown 标记和常见
// 请求前缀，再只保留第一句话；供新会话首次发送时立即生成乐观标题。
function cleanOptimisticTitleText(text: string): string {
  const lines: string[] = [];
  let inFence = false;
  for (const rawLine of text.split(/\r?\n/)) {
    const stripped = rawLine.trim();
    if (stripped.startsWith("```") || stripped.startsWith("~~~")) {
      inFence = !inFence;
      continue;
    }
    if (!inFence) {
      lines.push(stripped.replace(MARKDOWN_LEADING_PATTERN, ""));
    }
  }
  let cleaned = lines.join("\n");
  for (let round = 0; round < 3; round += 1) {
    const next = cleaned.replace(CONVERSATION_PREFIX_PATTERN, "");
    if (next === cleaned) {
      break;
    }
    cleaned = next;
  }
  return firstTitleSentence(cleaned);
}

// 与后端 _first_sentence 保持一致：取最早出现的句末符号之前的内容作为
// 第一句话；英文句号仅在后面是空白/换行/字符串结尾时视为句末；当前行没有
// 句末符号时，换行也视作句界，只取第一行。
function firstTitleSentence(text: string): string {
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }
    const endIndex = line.search(SENTENCE_END_PATTERN);
    if (endIndex >= 0) {
      const firstSentence = line.slice(0, endIndex).trim();
      if (firstSentence) {
        return firstSentence.replace(/\s+/g, " ").trim();
      }
    } else {
      return line.replace(/\s+/g, " ").trim();
    }
  }
  return "";
}

const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "cancel_requested"]);

function readCompletedSessionIds(userId: string): string[] {
  try {
    const raw = window.localStorage.getItem(`${COMPLETED_SESSIONS_STORAGE_PREFIX}${userId}`);
    if (!raw) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    // localStorage 数据损坏时安全回退为空集合。
    return [];
  }
}

function writeCompletedSessionIds(userId: string, sessionIds: string[]) {
  try {
    window.localStorage.setItem(
      `${COMPLETED_SESSIONS_STORAGE_PREFIX}${userId}`,
      JSON.stringify(sessionIds),
    );
  } catch {
    // localStorage 不可用（例如隐私模式）时静默降级，未读状态仅保留在当前页面。
  }
}

function activeRunStorageKey(userId: string, sessionId: string): string {
  return `${ACTIVE_RUN_STORAGE_PREFIX}${userId}:${sessionId}`;
}

function isActiveRunStatus(status: string): boolean {
  return ACTIVE_RUN_STATUSES.has(status);
}

function synthesizeActiveSession(run: RunSummary): SessionSummary {
  const title = summarizeOptimisticSessionTitle(run.query_summary);
  return {
    session_id: run.session_id,
    title,
    preview: run.query_summary,
    message_count: 1,
    last_message_at: run.started_at,
    has_summary: false,
  };
}

function matchesSessionKeyword(session: SessionSummary, keyword: string): boolean {
  if (!keyword) {
    return true;
  }
  const needle = keyword.toLowerCase();
  return session.session_id.toLowerCase().includes(needle)
    || session.title.toLowerCase().includes(needle)
    || session.preview.toLowerCase().includes(needle);
}

function isSessionWithinDateRange(
  value: string | null,
  range: { dateFrom?: string; dateTo?: string },
): boolean {
  if (!range.dateFrom && !range.dateTo) {
    return true;
  }
  if (!value) {
    return false;
  }
  if (range.dateFrom && value < range.dateFrom) {
    return false;
  }
  if (range.dateTo && value >= range.dateTo) {
    return false;
  }
  return true;
}
