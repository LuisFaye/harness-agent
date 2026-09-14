import {
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  LoaderCircle,
  PauseCircle,
} from "lucide-react";

import type { RunProjection } from "@/lib/stream/run-projection";

interface WorkingStatusBarProps {
  projection: RunProjection | null;
}

const phaseLabels: Record<string, string> = {
  preparing: "准备任务",
  executing: "执行任务",
  evaluating: "评估结果",
  responding: "生成回答",
  waiting_input: "等待补充",
  waiting_confirmation: "等待确认",
  completed: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
};

export function WorkingStatusBar({ projection }: WorkingStatusBarProps) {
  if (!projection || projection.status === "idle") {
    return null;
  }
  const state = projection.workingState;
  const budget = state?.budget;
  const phase = state?.phase ?? projection.status;
  const label = phaseLabels[phase] ?? "处理中";
  const action = state?.current_action || statusAction(projection.status);
  const Icon = statusIcon(projection.status);

  return (
    <section
      aria-live="polite"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t bg-muted/40 px-4 py-2.5 text-sm"
    >
      <span className="flex min-w-0 items-center gap-2 font-medium">
        <Icon
          aria-hidden="true"
          className={projection.status === "running" || projection.status === "responding"
            ? "size-4 animate-spin text-primary"
            : "size-4 text-primary"}
        />
        <span>{label}</span>
      </span>
      <span className="min-w-0 flex-1 truncate text-muted-foreground">{action}</span>
      {budget?.tool_calls !== undefined ? (
        <span className="tabular-nums text-xs text-muted-foreground">
          工具调用 {budget.tool_calls} 次
          {budget.tool_call_limit === undefined ? null : ` · 上限 ${budget.tool_call_limit} 次`}
        </span>
      ) : null}
      {budget?.elapsed_seconds !== undefined ? (
        <span className="tabular-nums text-xs text-muted-foreground">
          {budget.elapsed_seconds}s
        </span>
      ) : null}
    </section>
  );
}

function statusIcon(status: RunProjection["status"]) {
  if (status === "completed") {
    return CheckCircle2;
  }
  if (status === "failed") {
    return AlertTriangle;
  }
  if (status === "waiting_input" || status === "waiting_confirmation") {
    return PauseCircle;
  }
  if (status === "cancel_requested" || status === "cancelled") {
    return CircleDot;
  }
  return LoaderCircle;
}

function statusAction(status: RunProjection["status"]): string {
  return {
    running: "正在理解任务并选择下一步",
    responding: "正在组织最终回答",
    waiting_input: "需要你补充信息后继续",
    waiting_confirmation: "需要你确认高风险操作",
    cancel_requested: "正在等待当前步骤安全停止",
    cancelled: "任务已经停止",
    completed: "任务已完成",
    failed: "任务执行失败",
    idle: "",
  }[status];
}
