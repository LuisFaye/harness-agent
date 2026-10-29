import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { historicalRunProjection } from "@/features/runs/run-format";
import { useAuth } from "@/features/auth/auth-provider";
import {
  getRun,
  getSession,
  listRunActivity,
  listSessionArtifacts,
  listSessionAttachments,
  listSessionRuns,
  listSessions,
} from "@/lib/api/agent";
import {
  saveDatabaseConnection,
  type DatabaseConnectionWritePayload,
} from "@/lib/api/management";
import type {
  AskRequest,
  ConversationMessage,
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

import { useSessionAttachments, type PendingAttachment } from "./use-session-attachments";
import { useRunStop } from "./use-run-stop";
import { useSessionRunConnections } from "./use-session-run-connections";
export type { PendingAttachment } from "./use-session-attachments";
import { sessionDateRange, type SessionTimeFilter } from "./session-time-filter";

import {
  activeRunStorageKey, bindOptimisticUserRunId, isActiveRunStatus,
  isSessionWithinDateRange, matchesSessionKeyword, normalizeRunUsage,
  OPTIMISTIC_TITLE_FALLBACK, readCompletedSessionIds, redactVisibleDatabaseSecret,
  summarizeOptimisticSessionTitle, synthesizeActiveSession, writeCompletedSessionIds,
} from "./workspace-session-state";
let optimisticMessageId = -1;

export interface QueuedMessage {
  id: string;
  query: string;
  attachments: PendingAttachment[];
  editing?: boolean;
}

interface SendOptions {
  appendUserMessage?: boolean;
  suppressUserMessage?: boolean;
  attachmentIdsOverride?: string[];
  pendingAttachmentsOverride?: PendingAttachment[];
  requestOverride?: AskRequest;
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
  const connections = useSessionRunConnections(userId);
  const [projection, setProjection] = useState<RunProjection | null>(null);
  const [optimisticMessages, setOptimisticMessages] = useState<ConversationMessage[]>([]);
  const [optimisticTitle, setOptimisticTitle] = useState<string | null>(null);
  const {
    pendingAttachments, setPendingAttachments, uploadFiles, retryAttachment,
    cancelAttachmentUpload, removePendingAttachment, isUploading, uploadNotice,
  } = useSessionAttachments(userId, sessionId);
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
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [dismissedErrorRunId, setDismissedErrorRunId] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const pendingProjectionRef = useRef<RunProjection | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const lastRequestsRef = useRef(new Map<string, AskRequest>());
  const currentSessionRef = useRef(sessionId);
  const { isStopping, stopRun, observeStop, resetStop } = useRunStop(userId, sessionId, setErrorMessage);
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
    connections.publish(sessionId, next);
    const runId = next.runId;
    if (runId) {
      window.localStorage.setItem(activeRunStorageKey(userId, sessionId), runId);
      // 把 run_id 绑到本轮乐观用户消息上，后台落库后 React key 仍是 run:{id}:user，
      // 避免回复结束时用户气泡卸载重挂、滚动区跟着跳回顶部。
      setOptimisticMessages((current) => bindOptimisticUserRunId(current, sessionId, runId));
    }
    observeStop(next);
    if (currentSessionRef.current !== sessionId) return;
    pendingProjectionRef.current = next;
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
  }, [connections, observeStop, sessionId, userId]);

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
      queryClient.fetchQuery({ queryKey: ["session", userId, sessionId], queryFn: () => getSession(userId, sessionId), staleTime: 0 }),
      queryClient.invalidateQueries({ queryKey: ["artifacts", userId, sessionId] }),
      queryClient.invalidateQueries({ queryKey: ["sessions", userId] }),
      queryClient.invalidateQueries({
        queryKey: ["session-latest-run", userId, sessionId],
      }),
    ]);
    if (detailResult) {
      const hasPersistedUserMessage = detailResult.messages.some(
        (message) => message.role === "user",
      );
      if (hasPersistedUserMessage && currentSessionRef.current === sessionId) {
        setOptimisticTitle(null);
      }
      setOptimisticMessages((current) => current.filter(
        (optimistic) => optimistic.session_id !== sessionId || !detailResult.messages.some(
          (persisted) => persisted.role === optimistic.role
            && persisted.content === optimistic.content,
        ),
      ));
    }
  }, [queryClient, sessionId, userId]);

  const executeQuery = useCallback(async (
    query: string,
    options: SendOptions = {},
  ) => {
    const requestAttachments = options.pendingAttachmentsOverride
      ?? (options.requestOverride || options.appendUserMessage === false ? [] : pendingAttachments);
    const attachmentIds = options.requestOverride?.attachment_ids ?? options.attachmentIdsOverride ?? requestAttachments
      .filter((item) => item.attachment?.id && !item.error)
      .map((item) => item.attachment!.id);
    const normalizedQuery = query.trim() || (attachmentIds.length ? "请结合附件内容回答。" : "");
    if ((!normalizedQuery && !attachmentIds.length) || isRunning || abortControllerRef.current) {
      return;
    }
    if (requestAttachments.some((item) => item.uploading || item.error)) {
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
    const request: AskRequest = options.requestOverride ?? {
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
    lastRequestsRef.current.set(sessionId, request);
    resetStop();
    setErrorMessage(null);
    setDismissedErrorRunId(null);
    setProjection(createRunProjection());
    // 消息一旦发出就清空待发附件，避免等待回复期间缩略图仍占着输入框。
    if (appendUserMessage && !options.pendingAttachmentsOverride && !options.requestOverride) {
      setPendingAttachments([]);
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

    connections.start(sessionId, controller);
    try {
      const finalProjection = await streamAgentRun(request, {
        signal: controller.signal,
        onProjection: (next) => {
          if (connections.owns(sessionId, controller)) publishProjection(next);
        },
      });
      if (!connections.owns(sessionId, controller)) return;
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
      if (finalProjection.error && currentSessionRef.current === sessionId) {
        setErrorMessage(finalProjection.error.message);
      }
      window.localStorage.removeItem(activeRunStorageKey(userId, requestSessionId));
      settleLocalSession(requestSessionId);
      void refreshConversation().catch(() => {
        // 回复已完成，后台同步失败不应把成功回答改成失败或锁住输入框。
      });
    } catch (error) {
      if (abortControllerRef.current === controller && !(error instanceof DOMException && error.name === "AbortError")) {
        setErrorMessage(error instanceof Error ? error.message : "Agent 请求失败。");
        resetStop();
      }
    } finally {
      connections.finish(sessionId, controller);
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
        setIsRunning(false);
      }
    }
  }, [
    connections,
    isRunning,
    optimisticMessages,
    optimisticTitle,
    publishProjection,
    settleLocalSession,
    refreshConversation,
    resetStop,
    setPendingAttachments,
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
    if (queuedMessage || pendingAttachments.some((item) => item.uploading || item.error)) {
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
  }, [executeQuery, isRunning, pendingAttachments, queuedMessage, sessionId, setPendingAttachments]);

  const editQueuedMessage = useCallback((query: string) => {
    setQueuedMessages((current) => {
      const queued = current.get(sessionId);
      if (!queued || (!query.trim() && !queued.attachments.length)) return current;
      const next = new Map(current);
      next.set(sessionId, { ...queued, query: query.trim(), editing: false });
      return next;
    });
  }, [sessionId]);

  const setQueuedEditing = useCallback((editing: boolean) => {
    setQueuedMessages((current) => {
      const queued = current.get(sessionId);
      if (!queued) return current;
      const next = new Map(current);
      next.set(sessionId, { ...queued, editing });
      return next;
    });
  }, [sessionId]);

  const cancelQueuedMessage = useCallback(() => {
    setQueuedMessages((current) => {
      const next = new Map(current);
      next.delete(sessionId);
      return next;
    });
  }, [sessionId]);

  useEffect(() => {
    if (
      isRunning
      || !queuedMessage
      || queuedMessage.editing
      || queuedDispatchRef.current === queuedMessage.id
      || !projection
      || connections.projection(sessionId)?.runId !== projection.runId
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
  }, [connections, executeQuery, isRunning, projection, queuedMessage, sessionId]);

  // 切换会话时必须在绘制前清空本地状态，否则新建对话会短暂带上上一会话的乐观消息。
  useLayoutEffect(() => {
    currentSessionRef.current = sessionId;
    if (animationFrameRef.current !== null) window.cancelAnimationFrame(animationFrameRef.current);
    animationFrameRef.current = null;
    pendingProjectionRef.current = null;
    setProjection(connections.projection(sessionId));
    setOptimisticTitle(null);
    setErrorMessage(null);
    setDismissedErrorRunId(null);
    resumeAttemptedRef.current = "";
    abortControllerRef.current = connections.controller(sessionId);
    setIsRunning(Boolean(abortControllerRef.current));
    const cached = connections.projection(sessionId);
    if (cached) observeStop(cached);
  }, [connections, observeStop, sessionId, userId]);

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
    if (!sessionId || isRunning || connections.controller(sessionId) || latestRunQuery.isFetching) {
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
    const initial = connections.projection(sessionId);
    connections.start(sessionId, controller);
    void recoverAgentRun(
      userId,
      runId,
      initial?.runId === runId ? initial : createRunProjection({ runId }),
      { signal: controller.signal, onProjection: (next) => {
        if (connections.owns(sessionId, controller)) publishProjection(next);
      } },
    ).then(async (finalProjection) => {
      if (!connections.owns(sessionId, controller)) return;
      publishProjection(finalProjection);
      window.localStorage.removeItem(activeRunStorageKey(userId, sessionId));
      settleLocalSession(sessionId);
      void refreshConversation().catch(() => {
        // 回复已完成，后台同步失败不应把成功回答改成失败或锁住输入框。
      });
    }).catch((error: unknown) => {
      if (abortControllerRef.current === controller && !(error instanceof DOMException && error.name === "AbortError")) {
        setErrorMessage(error instanceof Error ? error.message : "运行恢复失败。");
        resetStop();
      }
    }).finally(() => {
      connections.finish(sessionId, controller);
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
        setIsRunning(false);
      }
    });
  }, [
    connections,
    isRunning,
    latestRunQuery.data?.items,
    latestRunQuery.isFetching,
    resetStop,
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
      || connections.controller(sessionId)
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
    connections,
    isRunning,
    latestRunQuery.isFetching,
    latestRunDetailQuery.data,
    latestRunDetailQuery.isLoading,
    sessionId,
    userId,
  ]);

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

  const messages = useMemo(() => {
    const combined = [
      ...(sessionDetail?.messages ?? []),
      ...optimisticMessages.filter((message) => message.session_id === sessionId
        && !(message.run_id && (sessionDetail?.messages ?? []).some((persisted) => (
          persisted.role === message.role
          && persisted.run_id === message.run_id
        )))),
    ];
    const collapsed: ConversationMessage[] = [];
    for (const item of combined) {
      const prev = collapsed.at(-1);
      if (prev && prev.role === "assistant" && item.role === "assistant") {
        collapsed[collapsed.length - 1] = item;
      } else {
        collapsed.push(item);
      }
    }
    return collapsed;
  }, [optimisticMessages, sessionDetail?.messages, sessionId]);
  const lastUserMessage = useMemo(() => [...messages].reverse().find(
    (message) => message.role === "user" && (message.content.trim() || message.attachment_ids?.length),
  ), [messages]);
  const repeatLastRequest = useCallback(async (regenerate: boolean) => {
    if (isRunning || !lastUserMessage) return;
    const latestRequest = lastRequestsRef.current.get(sessionId);
    const saved = !regenerate || (latestRequest && redactVisibleDatabaseSecret(latestRequest.query) === lastUserMessage.content)
      ? latestRequest : undefined;
    const request: AskRequest = saved ? { ...saved } : {
      query: lastUserMessage.content || "请结合附件内容回答。",
      user_id: userId,
      session_id: sessionId,
      client_messages: messages.slice(0, messages.indexOf(lastUserMessage))
        .filter((message) => ["user", "assistant", "system"].includes(message.role))
        .slice(-12)
        .map((message) => ({
          role: message.role as "user" | "assistant" | "system",
          content: message.content,
          created_at: message.created_at,
        })),
      attachment_ids: lastUserMessage.attachment_ids?.length
        ? lastUserMessage.attachment_ids
        : (lastUserMessage.attachments ?? []).map((item) => item.id),
    };
    // 重试保持原请求的附件和上下文，不消费输入框中新起草的附件。
    request.suppress_user_message = regenerate || Boolean(lastUserMessage.run_id)
      || Boolean(sessionDetail?.messages.some((message) => message.id === lastUserMessage.id));
    // 原地覆盖本轮旧的 Assistant 回复，避免重试后同一轮堆叠多个回复框。
    setOptimisticMessages((current) => {
      const next = [...current];
      for (let index = next.length - 1; index >= 0; index -= 1) {
        const item = next[index];
        if (item?.session_id !== sessionId) continue;
        if (item.role === "assistant") {
          next.splice(index, 1);
        } else {
          break;
        }
      }
      return next;
    });
    queryClient.setQueryData(
      ["session", userId, sessionId],
      (old: typeof sessionDetail) => {
        if (!old?.messages?.length) return old;
        let cutIndex = old.messages.length;
        while (cutIndex > 0 && old.messages[cutIndex - 1]?.role === "assistant") {
          cutIndex -= 1;
        }
        if (cutIndex === old.messages.length) return old;
        return {
          ...old,
          messages: old.messages.slice(0, cutIndex),
        };
      },
    );
    await executeQuery(request.query, { appendUserMessage: false, requestOverride: request });
  }, [executeQuery, isRunning, lastUserMessage, messages, queryClient, sessionDetail, sessionId, userId]);
  const retryLastRequest = useCallback(() => repeatLastRequest(false), [repeatLastRequest]);
  const regenerateLastAnswer = useCallback(() => {
    void repeatLastRequest(true);
  }, [repeatLastRequest]);
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
    isStopping,
    uploadNotice,
    errorMessage,
    sendQuery,
    confirmRun: () => executeQuery("确认", { appendUserMessage: false }),
    cancelConfirmation: () => executeQuery("取消", { appendUserMessage: false }),
    retryLastQuery: retryLastRequest,
    canRegenerateLastAnswer: Boolean(lastUserMessage),
    regenerateLastAnswer,
    contextObservation,
    latestRunUsage,
    stopRun,
    uploadFiles,
    retryAttachment,
    cancelAttachmentUpload,
    completedSessionIds,
    completionNotice,
    markSessionRead,
    dismissCompletionNotice,
    removePendingAttachment,
    editQueuedMessage,
    setQueuedEditing,
    cancelQueuedMessage,
    clearError: dismissRunError,
    connectDatabase,
  };
}
