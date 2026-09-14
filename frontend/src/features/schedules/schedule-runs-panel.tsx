import { ChevronRight, LoaderCircle, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ScheduledJob, ScheduledJobRun } from "@/lib/api/management-schemas";

import {
  compactScheduleText,
  formatScheduleDate,
  scheduleRunStatus,
} from "./schedule-format";
import { ScheduleStatusBadge } from "./schedule-status-badge";

interface ScheduleRunsPanelProps {
  job: ScheduledJob;
  runs: ScheduledJobRun[];
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

export function ScheduleRunsPanel({
  job,
  runs,
  loading,
  error,
  onClose,
}: ScheduleRunsPanelProps) {
  return (
    <section
      aria-labelledby="schedule-runs-title"
      className="mt-5 overflow-hidden rounded-xl border bg-card"
    >
      <header className="flex items-start justify-between gap-4 border-b bg-muted/35 px-4 py-3 sm:px-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-primary">任务 #{job.id}</p>
          <h2 className="mt-1 font-semibold" id="schedule-runs-title">{job.title} · 执行记录</h2>
        </div>
        <Button aria-label="关闭执行记录" onClick={onClose} size="icon" variant="ghost">
          <X aria-hidden="true" />
        </Button>
      </header>

      {loading ? (
        <div className="flex min-h-36 items-center justify-center gap-2 text-sm text-muted-foreground" role="status">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          正在读取执行记录
        </div>
      ) : error ? (
        <div className="m-4 rounded-lg border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive" role="alert">
          {error}
        </div>
      ) : runs.length ? (
        <ol className="divide-y">
          {runs.map((run) => {
            const status = scheduleRunStatus(run.status);
            return (
              <li className="grid gap-3 px-4 py-4 sm:grid-cols-[7rem_10rem_1fr] sm:px-5" key={run.id}>
                <div>
                  <span className="text-xs text-muted-foreground">运行 #{run.id}</span>
                  <div className="mt-1"><ScheduleStatusBadge {...status} /></div>
                </div>
                <div className="text-xs leading-6 text-muted-foreground">
                  <p>{formatScheduleDate(run.started_at)}</p>
                  <p>{formatScheduleDate(run.finished_at)}</p>
                </div>
                <div className="min-w-0">
                  <p className={run.error_message ? "text-sm text-destructive" : "text-sm text-foreground"}>
                    {compactScheduleText(run.output_text || run.error_message)}
                  </p>
                  <span className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
                    查看的是自动任务记录，不会混入 Agent Runs
                    <ChevronRight aria-hidden="true" className="size-3" />
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="px-5 py-12 text-center text-sm text-muted-foreground">这项任务还没有执行记录。</div>
      )}
    </section>
  );
}
