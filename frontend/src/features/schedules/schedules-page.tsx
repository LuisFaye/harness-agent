import { PageHeader } from "@/components/layout/page-header";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CalendarCheck, CirclePause, RefreshCw, Timer } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-provider";
import {
  listScheduleRuns,
  listSchedules,
  runScheduleNow,
  updateScheduleStatus,
} from "@/lib/api/management";
import type { ScheduledJob } from "@/lib/api/management-schemas";

import { ScheduleCard } from "./schedule-card";
import { ScheduleRunsPanel } from "./schedule-runs-panel";

export function SchedulesPage() {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const userId = user!.id;
  const [selectedJob, setSelectedJob] = useState<ScheduledJob | null>(null);
  const [notice, setNotice] = useState<{ text: string; tone: "success" | "danger" } | null>(null);
  const schedulesQuery = useQuery({
    queryKey: ["schedules", userId],
    queryFn: () => listSchedules(userId),
  });
  const runsQuery = useQuery({
    queryKey: ["schedule-runs", selectedJob?.id],
    queryFn: () => listScheduleRuns(selectedJob!.id),
    enabled: Boolean(selectedJob),
  });
  const actionMutation = useMutation({
    mutationFn: async ({ action, job }: {
      action: "pause" | "resume" | "run-now";
      job: ScheduledJob;
    }) => action === "run-now"
      ? runScheduleNow(userId, job.id)
      : updateScheduleStatus(userId, job.id, action),
    onSuccess: async (payload, variables) => {
      const runFailed = "run" in payload && payload.run.status !== "success";
      setNotice({
        text: variables.action === "run-now"
          ? runFailed
            ? `任务 #${variables.job.id} 执行失败，请查看运行记录。`
            : `任务 #${variables.job.id} 已执行，运行记录已刷新。`
          : `任务 #${variables.job.id} 已${variables.action === "pause" ? "暂停" : "恢复"}。`,
        tone: runFailed ? "danger" : "success",
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["schedules", userId] }),
        queryClient.invalidateQueries({ queryKey: ["schedule-runs", variables.job.id] }),
      ]);
    },
  });

  const jobs = schedulesQuery.data?.items ?? [];
  const activeCount = jobs.filter((item) => item.status === "enabled").length;
  const pausedCount = jobs.filter((item) => item.status === "paused").length;

  function handleAction(
    action: "pause" | "resume" | "run-now" | "history",
    job: ScheduledJob,
  ) {
    setNotice(null);
    if (action === "history") {
      setSelectedJob((current) => current?.id === job.id ? null : job);
      return;
    }
    actionMutation.mutate({ action, job });
  }

  return (
    <section aria-labelledby="schedules-title" className="mx-auto w-full max-w-6xl">
      <PageHeader title="自动任务" description="让重复工作按时完成，随时查看任务状态和执行记录。" icon={CalendarCheck} id="schedules-title">
        <Button
          disabled={schedulesQuery.isFetching}
          onClick={() => schedulesQuery.refetch()}
          variant="outline"
        >
          <RefreshCw aria-hidden="true" className={schedulesQuery.isFetching ? "animate-spin" : ""} />
          刷新任务
        </Button>
      </PageHeader>

      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        <SummaryCard icon={<CalendarCheck />} label="全部任务" value={jobs.length} />
        <SummaryCard icon={<Timer />} label="已启用" tone="success" value={activeCount} />
        <SummaryCard icon={<CirclePause />} label="已暂停" tone="warning" value={pausedCount} />
      </div>

      {notice ? (
        <div
          className={`mt-5 rounded-lg border px-4 py-3 text-sm ${notice.tone === "danger" ? "border-destructive/25 bg-destructive/5 text-destructive" : "border-success/25 bg-success/5 text-success"}`}
          role={notice.tone === "danger" ? "alert" : "status"}
        >
          {notice.text}
        </div>
      ) : null}
      {actionMutation.error ? (
        <div className="mt-5 rounded-lg border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm text-destructive" role="alert">
          {actionMutation.error.message}
        </div>
      ) : null}

      <div className="mt-6 flex items-end justify-between gap-4">
        <div>
          <h2 className="font-semibold">已创建任务</h2>
          <p className="mt-1 text-sm text-muted-foreground">任务按后端返回顺序展示。</p>
        </div>
        <span className="text-xs font-medium text-muted-foreground">{jobs.length} 个任务</span>
      </div>

      {schedulesQuery.isLoading ? (
        <div className="mt-4 grid min-h-48 place-items-center rounded-xl border bg-card text-sm text-muted-foreground" role="status">
          正在读取自动任务
        </div>
      ) : schedulesQuery.error ? (
        <div className="mt-4 rounded-xl border border-destructive/25 bg-destructive/5 p-5 text-sm text-destructive" role="alert">
          <div className="flex items-center gap-2 font-semibold"><AlertCircle aria-hidden="true" />任务加载失败</div>
          <p className="mt-2">{schedulesQuery.error.message}</p>
        </div>
      ) : jobs.length ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          {jobs.map((job) => (
            <ScheduleCard
              busyAction={actionMutation.isPending
                ? `${actionMutation.variables?.action}:${actionMutation.variables?.job.id}`
                : null}
              job={job}
              key={job.id}
              onAction={handleAction}
              selected={selectedJob?.id === job.id}
            />
          ))}
        </div>
      ) : (
        <div className="mt-4 grid min-h-56 place-items-center rounded-xl border border-dashed bg-card px-6 text-center">
          <div>
            <CalendarCheck aria-hidden="true" className="mx-auto size-8 text-muted-foreground" />
            <h2 className="mt-3 font-semibold">暂无自动任务</h2>
            <p className="mt-1 text-sm text-muted-foreground">创建后的真实调度任务会在这里统一展示。</p>
          </div>
        </div>
      )}

      {selectedJob ? (
        <ScheduleRunsPanel
          error={runsQuery.error?.message ?? null}
          job={selectedJob}
          loading={runsQuery.isLoading}
          onClose={() => setSelectedJob(null)}
          runs={runsQuery.data?.items ?? []}
        />
      ) : null}
    </section>
  );
}

function SummaryCard({
  icon,
  label,
  value,
  tone = "neutral",
}: {
  icon: React.ReactElement;
  label: string;
  value: number;
  tone?: "neutral" | "success" | "warning";
}) {
  const toneClass = tone === "success"
    ? "bg-success/10 text-success"
    : tone === "warning"
      ? "bg-warning/10 text-warning"
      : "bg-accent text-accent-foreground";
  return (
    <article className="flex items-center gap-3 rounded-xl border bg-card p-4">
      <span aria-hidden="true" className={`grid size-10 place-items-center rounded-lg [&>svg]:size-5 ${toneClass}`}>{icon}</span>
      <div>
        <strong className="block text-xl tabular-nums">{value}</strong>
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
    </article>
  );
}
