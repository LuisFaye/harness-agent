import { getRunEvents } from "@/lib/api/agent";
import { apiUrl, responseToApiError } from "@/lib/api/client";
import type { AskRequest, RunEventListResponse } from "@/lib/api/schemas";

import { NdjsonByteDecoder } from "./ndjson";
import {
  applyRunEvent,
  applyStoredRunEvent,
  createRunProjection,
  type RunProjection,
} from "./run-projection";
import type { StreamEvent } from "./schemas";

const TERMINAL_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "waiting_input",
  "waiting_confirmation",
]);

interface StreamRunOptions {
  signal?: AbortSignal;
  onProjection?: (projection: RunProjection) => void;
  fetchImpl?: typeof fetch;
  fetchEventPage?: (
    userId: string,
    runId: string,
    afterSequence: number,
  ) => Promise<RunEventListResponse>;
  wait?: (milliseconds: number) => Promise<void>;
  maxRecoveryAttempts?: number;
}

export async function streamAgentRun(
  request: AskRequest,
  options: StreamRunOptions = {},
): Promise<RunProjection> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const response = await fetchImpl(apiUrl("/ask/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    ...(options.signal ? { signal: options.signal } : {}),
  });
  if (!response.ok) {
    throw await responseToApiError(response);
  }
  if (!response.body) {
    throw new Error("浏览器不支持流式响应，请升级浏览器后重试。");
  }

  const decoder = new NdjsonByteDecoder();
  const reader = response.body.getReader();
  let projection = createRunProjection({
    runId: response.headers.get("X-Run-Id")?.trim() ?? "",
  });
  if (projection.runId) {
    options.onProjection?.(projection);
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      const events = decoder.push(value);
      projection = applyEvents(projection, events, options.onProjection);
      if (events.some((event) => ["run_completed", "run_failed", "run_cancelled"].includes(event.type))) {
        // 终态是业务完成标志，不再等待代理服务器关闭 HTTP 连接。
        void reader.cancel().catch(() => {});
        return projection;
      }
    }
    projection = applyEvents(projection, decoder.finish(), options.onProjection);
  } catch (error) {
    if (isAbortError(error) || !projection.runId) {
      throw error;
    }
    return recoverAgentRun(request.user_id, projection.runId, projection, options);
  }

  if (isTerminalProjection(projection)) {
    return projection;
  }
  if (!projection.runId) {
    throw new Error("流式响应异常结束，未收到运行编号。");
  }
  return recoverAgentRun(request.user_id, projection.runId, projection, options);
}

export async function recoverAgentRun(
  userId: string,
  runId: string,
  initial: RunProjection = createRunProjection({ runId }),
  options: StreamRunOptions = {},
): Promise<RunProjection> {
  const fetchEventPage = options.fetchEventPage ?? getRunEvents;
  const wait = options.wait ?? ((milliseconds) => new Promise<void>((resolve) => {
    window.setTimeout(resolve, milliseconds);
  }));
  const maxAttempts = options.maxRecoveryAttempts ?? Number.POSITIVE_INFINITY;
  let projection = initial;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (options.signal?.aborted) {
      throw new DOMException("请求已取消。", "AbortError");
    }
    const page = await fetchEventPage(userId, runId, projection.lastSequence);
    projection = applyEvents(projection, page.events, options.onProjection, applyStoredRunEvent);
    if (isTerminalProjection(projection)) {
      return projection;
    }
    if (page.has_more) {
      continue;
    }
    await wait(500);
  }
  throw new Error("连接已断开，暂时无法自动恢复。请到运行监控查看后台状态。");
}

export function isTerminalProjection(projection: RunProjection): boolean {
  return TERMINAL_STATUSES.has(projection.status);
}

function applyEvents(
  initial: RunProjection,
  events: StreamEvent[],
  onProjection?: (projection: RunProjection) => void,
  projectEvent = applyRunEvent,
): RunProjection {
  return events.reduce((projection, event) => {
    const next = projectEvent(projection, event);
    if (next !== projection) {
      onProjection?.(next);
    }
    return next;
  }, initial);
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}
