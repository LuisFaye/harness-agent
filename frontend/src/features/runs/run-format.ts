import type { RunDetail } from "@/lib/api/schemas";
import type { RunProjection } from "@/lib/stream/run-projection";
import { traceItemSchema, type TraceItem } from "@/lib/stream/schemas";

const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  needs_input: "等待补充",
  needs_confirmation: "等待确认",
  success: "成功",
  error: "失败",
  max_iterations: "达到轮次上限",
  cancel_requested: "正在取消",
  cancelled: "已取消",
};

const sourceLabels: Record<string, string> = {
  agent_loop: "Agent Loop",
  skill_command: "Skill 命令",
  local: "本地执行",
  error: "错误兜底",
};

export function runStatusLabel(status: string): string {
  return statusLabels[status] ?? (status || "未知");
}

export function runStatusTone(status: string): "success" | "danger" | "warning" | "info" {
  if (status === "success") {
    return "success";
  }
  if (status === "error" || status === "max_iterations") {
    return "danger";
  }
  if (status === "needs_input" || status === "needs_confirmation") {
    return "warning";
  }
  return "info";
}

export function executionSourceLabel(source: string): string {
  return sourceLabels[source] ?? (source || "未知");
}

export function formatRunDate(value: string | null): string {
  if (!value) {
    return "暂无时间";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "暂无数据";
  }
  if (value < 1000) {
    return `${Math.round(value)} ms`;
  }
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} 秒`;
}

export function formatToken(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "暂无数据"
    : value.toLocaleString("zh-CN");
}

export function parseRunTrace(items: Record<string, unknown>[]): TraceItem[] {
  return items.flatMap((item, index) => {
    const result = traceItemSchema.safeParse({
      key: item.key || `historical-trace-${index}`,
      stage: item.stage || "historical",
      status: item.status || "unknown",
      title: item.title || "执行事件",
      summary: item.summary || "",
      details: isRecord(item.details) ? item.details : {},
    });
    return result.success ? [result.data] : [];
  });
}

export function historicalRunProjection(detail: RunDetail): RunProjection {
  return {
    runId: detail.run_id,
    lastSequence: 0,
    status: projectionStatus(detail.status),
    startedAt: detail.started_at,
    updatedAt: detail.finished_at || detail.started_at,
    workingState: detail.working_state,
    timelineItems: parseRunTrace(detail.trace),
    streamedText: detail.response_text,
    responseCompleted: Boolean(detail.response_text),
    citationBundle: detail.citation_bundle ?? null,
    interaction: null,
    terminalResult: null,
    error: detail.error_message
      ? {
        message: detail.error_message,
        code: detail.error_stage,
        stage: detail.error_stage,
        retriable: false,
      }
      : null,
    missingInfo: detail.missing_info,
    origin: "history",
  };
}

function projectionStatus(status: string): RunProjection["status"] {
  const mapping: Record<string, RunProjection["status"]> = {
    queued: "running",
    running: "running",
    needs_input: "waiting_input",
    needs_confirmation: "waiting_confirmation",
    success: "completed",
    error: "failed",
    max_iterations: "failed",
    cancel_requested: "cancel_requested",
    cancelled: "cancelled",
  };
  return mapping[status] ?? "idle";
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function textValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}
