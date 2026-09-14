import { state } from "./state.js";
import {
  appendResponseDelta,
  markCancelRequested,
  markStreamReconnecting,
  normalizeTraceItem,
  replaceStreamingResponse,
  upsertLiveTrace,
} from "./trace.js";
import { normalizeStreamEvent } from "./stream-events.js";
import { recoverPersistedEvents } from "./stream-recovery.js";
import { updateWorkingState } from "./status-bar.js";

export async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.error || payload.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new Error(message || `请求失败（${response.status}）`);
  }
  return payload;
}

export async function requestChatStream(query) {
  const controller = new AbortController();
  state.currentAbortController = controller;
  const response = await fetch("/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: controller.signal,
    body: JSON.stringify({
      query,
      user_id: state.userId,
      session_id: state.sessionId,
      client_messages: buildClientMessagesForRequest(query),
      attachment_ids: [...state.selectedAttachmentIds],
    }),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload.error || payload.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new Error(message || `请求失败（${response.status}）`);
  }

  const responseRunId = String(response.headers.get("X-Run-Id") || "").trim();
  if (state.activeRun && responseRunId) {
    state.activeRun.runId = responseRunId;
  }

  if (!response.body) {
    return request("/ask", {
      method: "POST",
      body: JSON.stringify({
        query,
        user_id: state.userId,
        session_id: state.sessionId,
        client_messages: buildClientMessagesForRequest(query),
        attachment_ids: [...state.selectedAttachmentIds],
      }),
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload = null;
  const streamCursor = { runId: "", lastSequence: 0 };

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const payload = consumeStreamBuffer(buffer, streamCursor);
      buffer = payload.tail;
      if (payload.finalPayload) {
        finalPayload = payload.finalPayload;
      }
      if (done) {
        break;
      }
    }

    const tail = consumeStreamBuffer(`${buffer}\n`, streamCursor);
    if (tail.finalPayload) {
      finalPayload = tail.finalPayload;
    }
  } catch (error) {
    if (error?.name === "AbortError" || !streamCursor.runId && !responseRunId) {
      throw error;
    }
    markStreamReconnecting();
    finalPayload = await recoverRunEvents(
      streamCursor.runId || responseRunId,
      streamCursor,
      finalPayload,
    );
  }

  if (!finalPayload && (streamCursor.runId || responseRunId)) {
    markStreamReconnecting();
    finalPayload = await recoverRunEvents(
      streamCursor.runId || responseRunId,
      streamCursor,
      finalPayload,
    );
  }

  state.currentAbortController = null;
  if (finalPayload) {
    finalPayload.trace = Array.isArray(finalPayload.trace)
      ? finalPayload.trace.map((item, index) => normalizeTraceItem(item, index))
      : [];
    return finalPayload;
  }
  throw new Error("流式响应异常结束，未收到最终结果。");
}

export async function uploadAttachment(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("user_id", state.userId);
  form.append("session_id", state.sessionId);
  const response = await fetch("/attachments", { method: "POST", body: form });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.error || payload.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message || "附件上传失败");
  }
  return payload;
}

export async function listAttachments() {
  return request(`/attachments?user_id=${encodeURIComponent(state.userId)}&session_id=${encodeURIComponent(state.sessionId)}`);
}

export async function cancelRun(runId) {
  return request(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({ user_id: state.userId }),
  });
}

export function consumeStreamBuffer(buffer, streamCursor = { runId: "", lastSequence: 0 }) {
  let tail = buffer;
  let finalPayload = null;
  let newlineIndex = tail.indexOf("\n");
  while (newlineIndex >= 0) {
    const line = tail.slice(0, newlineIndex).trim();
    tail = tail.slice(newlineIndex + 1);
    if (line) {
      const event = JSON.parse(line);
      finalPayload = consumeStreamEvent(event, finalPayload, streamCursor);
    }
    newlineIndex = tail.indexOf("\n");
  }
  return { tail, finalPayload };
}

function consumeStreamEvent(event, finalPayload, streamCursor) {
  const normalized = normalizeStreamEvent(event, streamCursor);
  // M2 期间保留旧协议读取能力，方便前后端滚动升级；新服务端只发送 v2。
  if (normalized.legacy) {
    return consumeLegacyStreamEvent(event, finalPayload);
  }
  if (normalized.ignored) {
    return finalPayload;
  }
  Object.assign(streamCursor, normalized.cursor);
  const { type, runId, sequence, payload } = normalized.event;
  if (state.activeRun) {
    state.activeRun.runId = runId;
    state.activeRun.lastSequence = sequence;
  }

  if (type === "trace_updated") {
    upsertLiveTrace(payload.trace || {});
  } else if (type === "status_updated") {
    updateWorkingState(payload.state || {});
  } else if (type === "response_delta") {
    appendResponseDelta(payload.delta || "");
  } else if (type === "response_block") {
    appendResponseDelta(payload.delta || "");
  } else if (type === "response_completed") {
    replaceStreamingResponse(payload.text || "");
  } else if (type === "run_completed") {
    return payload.result || finalPayload;
  } else if (type === "run_cancel_requested") {
    markCancelRequested("正在等待当前执行步骤结束...");
  } else if (type === "run_cancelled") {
    return payload.result || {
      run_id: runId,
      status: "cancelled",
      response_text: "任务已取消。",
      stop_reason: payload.reason || "用户取消任务",
      missing_info: [],
      plan: null,
      observations: [],
      trace: [],
    };
  } else if (type === "run_failed") {
    throw new Error(payload.message || "请求失败");
  }
  return finalPayload;
}

async function recoverRunEvents(runId, streamCursor, initialPayload) {
  return recoverPersistedEvents({
    runId,
    userId: state.userId,
    cursor: streamCursor,
    initialPayload,
    fetchPage: ({ runId: currentRunId, userId, afterSequence, limit }) => request(
      `/runs/${encodeURIComponent(currentRunId)}/events?user_id=${encodeURIComponent(userId)}&after_sequence=${afterSequence}&limit=${limit}`,
    ),
    consumeEvent: consumeStreamEvent,
    wait,
  });
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function consumeLegacyStreamEvent(event, finalPayload) {
  if (event.type === "trace") {
    upsertLiveTrace(event.trace || {});
  } else if (event.type === "response_delta") {
    appendResponseDelta(event.delta || "");
  } else if (event.type === "response_replace") {
    replaceStreamingResponse(event.text || "");
  } else if (event.type === "result") {
    return event.payload;
  } else if (event.type === "error") {
    throw new Error(event.message || "请求失败");
  }
  return finalPayload;
}

function buildClientMessagesForRequest(query) {
  const messages = Array.isArray(state.messages) ? [...state.messages] : [];
  const normalizedQuery = normalizeMessageContent(query);
  if (messages.length) {
    const last = messages[messages.length - 1];
    if (last?.role === "user" && normalizeMessageContent(last.content) === normalizedQuery) {
      messages.pop();
    }
  }
  return messages
    .filter((item) => ["user", "assistant", "system"].includes(item?.role) && normalizeMessageContent(item.content))
    .slice(-12)
    .map((item) => ({
      role: item.role,
      content: normalizeMessageContent(item.content),
      created_at: item.created_at || null,
    }));
}

function normalizeMessageContent(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}
