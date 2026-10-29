import { CalendarClock, History, Pause, Play, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ScheduledJob } from "@/lib/api/management-schemas";

import {
  formatScheduleDate,
  scheduleConfigSummary,
  scheduleKindLabel,
  scheduleStatus,
} from "./schedule-format";
import { ScheduleStatusBadge } from "./schedule-status-badge";

interface ScheduleCardProps {
  job: ScheduledJob;
  busyAction: string | null;
  selected: boolean;
  onAction: (action: "pause" | "resume" | "run-now" | "history", job: ScheduledJob) => void;
}

export function ScheduleCard({ job, busyAction, selected, onAction }: ScheduleCardProps) {
  const status = scheduleStatus(job.status);
  const summaries = scheduleConfigSummary(job.config_json);
  const paused = job.status === "paused";
  const busy = busyAction?.endsWith(`:${job.id}`) ?? false;

  return (
    <article className={`flex h-full flex-col rounded-2xl border bg-card p-5 shadow-2xs transition-all duration-150 hover:-translate-y-0.5 hover:border-primary/35 hover:shadow-md ${selected ? "border-primary ring-2 ring-primary/15" : "border-border/85"}`}>
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-mono text-xs font-medium text-muted-foreground">任务 #{job.id} · {job.job_type}</p>
          <h2 className="mt-1 truncate text-base font-semibold tracking-tight" title={job.title}>{job.title || "未命名任务"}</h2>
        </div>
        <ScheduleStatusBadge {...status} />
      </header>

      <dl className="mt-4 grid gap-2.5 rounded-xl border border-border/60 bg-muted/20 p-3.5 text-sm">
        <div className="grid grid-cols-[5.5rem_1fr] gap-3">
          <dt className="text-xs text-muted-foreground">执行周期</dt>
          <dd className="text-xs font-medium">{scheduleKindLabel(job)}</dd>
        </div>
        <div className="grid grid-cols-[5.5rem_1fr] gap-3">
          <dt className="text-xs text-muted-foreground">下次运行</dt>
          <dd className="text-xs font-medium">{formatScheduleDate(job.next_run_at)}</dd>
        </div>
        <div className="grid grid-cols-[5.5rem_1fr] gap-3">
          <dt className="text-xs text-muted-foreground">最近执行</dt>
          <dd className="text-xs">{formatScheduleDate(job.last_run_at)}</dd>
        </div>
        <div className="grid grid-cols-[5.5rem_1fr] gap-3">
          <dt className="text-xs text-muted-foreground">时区</dt>
          <dd className="font-mono text-xs">{job.timezone}</dd>
        </div>
      </dl>

      {summaries.length ? (
        <ul className="mt-3.5 space-y-1 rounded-xl border border-border/50 bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {summaries.map((summary) => <li className="truncate" key={summary} title={summary}>{summary}</li>)}
        </ul>
      ) : null}

      <div className="mt-auto flex flex-wrap gap-2 border-t border-border/50 pt-4">
        <Button
          disabled={busy}
          onClick={() => onAction("run-now", job)}
          size="sm"
          variant="outline"
        >
          <Play aria-hidden="true" />
          立即执行
        </Button>
        <Button
          disabled={busy}
          onClick={() => onAction(paused ? "resume" : "pause", job)}
          size="sm"
          variant="outline"
        >
          {paused ? <RotateCcw aria-hidden="true" /> : <Pause aria-hidden="true" />}
          {paused ? "恢复" : "暂停"}
        </Button>
        <Button
          aria-pressed={selected}
          onClick={() => onAction("history", job)}
          size="sm"
          variant="ghost"
        >
          <History aria-hidden="true" />
          执行记录
        </Button>
      </div>

      {busy ? (
        <span className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
          <CalendarClock aria-hidden="true" className="size-3.5 animate-pulse" />
          正在处理任务操作
        </span>
      ) : null}
    </article>
  );
}
