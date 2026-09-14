import {
  streamPayloadSchemas,
  type StreamEvent,
  type TraceItem,
  type WorkingState,
  type CitationBundle,
} from "./schemas";

export type RunProjectionStatus =
  | "idle"
  | "running"
  | "responding"
  | "waiting_input"
  | "waiting_confirmation"
  | "cancel_requested"
  | "cancelled"
  | "completed"
  | "failed";

export type RunProjectionOrigin = "stream" | "history";

export interface RunProjection {
  runId: string;
  lastSequence: number;
  status: RunProjectionStatus;
  startedAt: string | null;
  updatedAt: string | null;
  workingState: WorkingState | null;
  timelineItems: TraceItem[];
  streamedText: string;
  responseCompleted: boolean;
  citationBundle: CitationBundle | null;
  missingInfo: string[];
  interaction: DatabaseConnectionInteraction | null;
  terminalResult: Record<string, unknown> | null;
  origin: RunProjectionOrigin;
  error: {
    code: string | null;
    message: string;
    stage: string | null;
    retriable: boolean;
  } | null;
}

export interface DatabaseConnectionInteraction {
  type: "database_connection";
  request_id: string;
  title: string;
  description: string;
  name: string;
  host: string;
  port: number;
  database: string;
  username: string;
  scope: "agent";
}

export class StreamSequenceGapError extends Error {
  constructor(
    public readonly expectedSequence: number,
    public readonly receivedSequence: number,
  ) {
    super(`流式事件缺少第 ${expectedSequence} 段，实际收到第 ${receivedSequence} 段。`);
    this.name = "StreamSequenceGapError";
  }
}

export class StreamRunMismatchError extends Error {
  constructor(
    public readonly expectedRunId: string,
    public readonly receivedRunId: string,
  ) {
    super(`当前运行是 ${expectedRunId}，却收到了 ${receivedRunId} 的事件。`);
    this.name = "StreamRunMismatchError";
  }
}

export function createRunProjection(
  initial: Partial<Pick<RunProjection, "runId" | "lastSequence">> = {},
): RunProjection {
  return {
    runId: initial.runId ?? "",
    lastSequence: initial.lastSequence ?? 0,
    status: "idle",
    startedAt: null,
    updatedAt: null,
    workingState: null,
    timelineItems: [],
    streamedText: "",
    responseCompleted: false,
    citationBundle: null,
    missingInfo: [],
    interaction: null,
    terminalResult: null,
    origin: "stream",
    error: null,
  };
}

export function applyRunEvent(state: RunProjection, event: StreamEvent): RunProjection {
  return projectRunEvent(state, event, true);
}

export function applyStoredRunEvent(state: RunProjection, event: StreamEvent): RunProjection {
  // 持久化会省略高频事件；保留原始游标，但不要求相邻编号连续。
  return projectRunEvent(state, event, false);
}

function projectRunEvent(state: RunProjection, event: StreamEvent, requireContinuous: boolean): RunProjection {
  if (state.runId && state.runId !== event.run_id) {
    throw new StreamRunMismatchError(state.runId, event.run_id);
  }
  if (event.sequence <= state.lastSequence) {
    return state;
  }
  const expectedSequence = state.lastSequence + 1;
  if (requireContinuous && event.sequence !== expectedSequence) {
    throw new StreamSequenceGapError(expectedSequence, event.sequence);
  }

  const next: RunProjection = {
    ...state,
    runId: event.run_id,
    lastSequence: event.sequence,
    updatedAt: event.timestamp,
  };

  switch (event.type) {
    case "run_started":
      return {
        ...next,
        status: "running",
        startedAt: event.timestamp,
        error: null,
      };
    case "status_updated": {
      const payload = streamPayloadSchemas.status_updated.parse(event.payload);
      return {
        ...next,
        status: statusFromWorkingPhase(payload.state.phase, next.status),
        workingState: payload.state,
      };
    }
    case "trace_updated": {
      const payload = streamPayloadSchemas.trace_updated.parse(event.payload);
      return {
        ...next,
        timelineItems: upsertTimelineItem(next.timelineItems, payload.trace),
      };
    }
    case "tool_started":
    case "tool_finished":
      return next;
    case "response_started":
      return { ...next, status: "responding" };
    case "response_delta": {
      const payload = streamPayloadSchemas.response_delta.parse(event.payload);
      const fullText = typeof payload.text === "string" ? payload.text : "";
      return {
        ...next,
        status: "responding",
        streamedText: fullText.length > 0 ? fullText : `${next.streamedText}${payload.delta}`,
        citationBundle: payload.citation_bundle ?? next.citationBundle,
      };
    }
    case "response_block": {
      const payload = streamPayloadSchemas.response_block.parse(event.payload);
      const fullText = typeof payload.text === "string" ? payload.text : "";
      const delta = payload.delta || payload.block?.markdown || "";
      const streamedText = fullText.length > 0 ? fullText : `${next.streamedText}${delta}`;
      return {
        ...next,
        status: "responding",
        streamedText,
        citationBundle: payload.citation_bundle ?? next.citationBundle,
      };
    }
    case "response_completed": {
      const payload = streamPayloadSchemas.response_completed.parse(event.payload);
      // 终态文本与流式内容一致时保留已有 DOM，避免 Streamdown 整段重解析。
      const textChanged = normalizeResponseText(payload.text) !== normalizeResponseText(next.streamedText);
      return {
        ...next,
        status: "responding",
        streamedText: textChanged ? payload.text : next.streamedText,
        responseCompleted: true,
        citationBundle: payload.citation_bundle ?? next.citationBundle,
      };
    }
    case "run_waiting": {
      const payload = streamPayloadSchemas.run_waiting.parse(event.payload);
      return {
        ...next,
        status: payload.status === "needs_confirmation"
          ? "waiting_confirmation"
          : "waiting_input",
        missingInfo: payload.missing_info,
        interaction: payload.interaction ?? null,
      };
    }
    case "run_cancel_requested":
      return { ...next, status: "cancel_requested" };
    case "run_cancelled": {
      const payload = streamPayloadSchemas.run_cancelled.parse(event.payload);
      return {
        ...next,
        status: "cancelled",
        terminalResult: payload.result,
      };
    }
    case "run_failed": {
      const payload = streamPayloadSchemas.run_failed.parse(event.payload);
      return {
        ...next,
        status: "failed",
        error: {
          code: payload.code ?? null,
          message: payload.message,
          stage: payload.stage ?? null,
          retriable: payload.retriable ?? false,
        },
      };
    }
    case "run_completed": {
      const payload = streamPayloadSchemas.run_completed.parse(event.payload);
      return {
        ...next,
        status: statusFromTerminalResult(payload.status),
        terminalResult: payload.result,
      };
    }
  }
}

export function reduceRunEvents(
  events: StreamEvent[],
  initial: RunProjection = createRunProjection(),
): RunProjection {
  return events.reduce(applyRunEvent, initial);
}

function upsertTimelineItem(items: TraceItem[], item: TraceItem): TraceItem[] {
  const identity = timelineIdentity(item);
  const index = items.findIndex((current) => timelineIdentity(current) === identity);
  if (index < 0) {
    return [...items, item];
  }
  return items.map((current, currentIndex) => currentIndex === index ? item : current);
}

function timelineIdentity(item: TraceItem): string {
  const tool = typeof item.details?.tool === "string" ? item.details.tool : "";
  const iteration = typeof item.details?.iteration === "number" ? item.details.iteration : null;
  if (tool && iteration !== null && ["tool_started", "tool_finished"].includes(item.stage)) {
    return `tool:${tool}:${iteration}`;
  }
  return item.key;
}

function statusFromWorkingPhase(
  phase: string,
  fallback: RunProjectionStatus,
): RunProjectionStatus {
  return {
    preparing: "running",
    executing: "running",
    evaluating: "running",
    responding: "responding",
    waiting_input: "waiting_input",
    waiting_confirmation: "waiting_confirmation",
    cancelled: "cancelled",
    completed: "completed",
    failed: "failed",
  }[phase] as RunProjectionStatus | undefined ?? fallback;
}

function statusFromTerminalResult(status: string): RunProjectionStatus {
  return {
    success: "completed",
    needs_input: "waiting_input",
    needs_confirmation: "waiting_confirmation",
    cancel_requested: "cancel_requested",
    cancelled: "cancelled",
  }[status] as RunProjectionStatus | undefined ?? "failed";
}

function normalizeResponseText(text: string): string {
  return text.replace(/\r\n/g, "\n").trim();
}
