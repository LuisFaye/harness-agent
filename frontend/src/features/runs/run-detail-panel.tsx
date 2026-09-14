import {
  ArrowLeft,
  Bot,
  Clock3,
  ExternalLink,
  LoaderCircle,
  RefreshCw,
  Square,
} from "lucide-react";
import { lazy, Suspense, useMemo } from "react";
import { Link } from "react-router-dom";

import { ExecutionTimeline } from "@/features/agent/execution-timeline";
import { WorkingStatusBar } from "@/features/agent/working-status-bar";
import { Button } from "@/components/ui/button";
import type { RunDetail } from "@/lib/api/schemas";

import {
  executionSourceLabel,
  formatDuration,
  formatRunDate,
  formatToken,
  historicalRunProjection,
  parseRunTrace,
} from "./run-format";
import {
  ObservationsSection,
  PlanSection,
  PromptSnapshotSection,
  RunErrorSection,
  WorkingStateDetails,
} from "./run-detail-sections";
import { ModelRequestsSection } from "./model-requests-section";
import { RunArtifactsSection } from "./run-artifacts-section";
import { RunStatusBadge } from "./run-status-badge";

const CitationMessageResponse = lazy(async () => ({
  default: (await import("@/features/citations/citation-message-response")).CitationMessageResponse,
}));

interface RunDetailPanelProps {
  runId: string;
  detail: RunDetail | null;
  loading: boolean;
  fetching: boolean;
  error: unknown;
  cancelPending: boolean;
  cancelError: unknown;
  onBack: () => void;
  onRefresh: () => void;
  onCancel: () => void;
}

const ACTIVE_STATUSES = new Set(["queued", "running", "cancel_requested"]);

export function RunDetailPanel({
  runId,
  detail,
  loading,
  fetching,
  error,
  cancelPending,
  cancelError,
  onBack,
  onRefresh,
  onCancel,
}: RunDetailPanelProps) {
  if (!runId) {
    return <RunDetailEmpty />;
  }
  if (loading) {
    return <RunDetailLoading runId={runId} />;
  }
  if (error || !detail) {
    return (
      <div className="grid h-full place-items-center p-6 text-center" role="alert">
        <div>
          <strong>运行详情加载失败</strong>
          <p className="mt-1 text-sm text-muted-foreground">
            {error instanceof Error ? error.message : `没有找到 ${runId}`}
          </p>
          <Button className="mt-4" onClick={onRefresh} variant="outline">
            <RefreshCw aria-hidden="true" />
            重试
          </Button>
        </div>
      </div>
    );
  }
  return (
    <RunDetailContent
      cancelError={cancelError}
      cancelPending={cancelPending}
      detail={detail}
      fetching={fetching}
      onBack={onBack}
      onCancel={onCancel}
      onRefresh={onRefresh}
    />
  );
}

function RunDetailContent({
  detail,
  fetching,
  cancelPending,
  cancelError,
  onBack,
  onRefresh,
  onCancel,
}: {
  detail: RunDetail;
  fetching: boolean;
  cancelPending: boolean;
  cancelError: unknown;
  onBack: () => void;
  onRefresh: () => void;
  onCancel: () => void;
}) {
  const projection = useMemo(() => historicalRunProjection(detail), [detail]);
  const trace = useMemo(() => parseRunTrace(detail.trace), [detail.trace]);
  return (
    <article className="h-full min-h-0 min-w-0 overflow-y-auto bg-card" tabIndex={-1}>
      <header className="sticky top-0 z-10 flex min-h-16 items-center gap-3 border-b bg-card/95 px-4 backdrop-blur sm:px-6">
        <Button aria-label="返回运行列表" className="lg:hidden" onClick={onBack} size="icon" variant="ghost">
          <ArrowLeft aria-hidden="true" />
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <RunStatusBadge status={detail.status} />
            {detail.legacy_record ? (
              <span className="rounded-md bg-warning/10 px-2 py-1 text-xs text-warning">旧记录</span>
            ) : null}
          </div>
          <h1 className="mt-1 truncate text-base font-semibold">运行详情</h1>
          <code className="block truncate text-xs text-muted-foreground">{detail.run_id}</code>
        </div>
        {ACTIVE_STATUSES.has(detail.status) ? (
          <Button disabled={cancelPending || detail.status === "cancel_requested"} onClick={onCancel} variant="outline">
            {cancelPending
              ? <LoaderCircle aria-hidden="true" className="animate-spin" />
              : <Square aria-hidden="true" />}
            停止
          </Button>
        ) : null}
        <Button aria-label="刷新运行详情" disabled={fetching} onClick={onRefresh} size="icon" variant="ghost">
          <RefreshCw aria-hidden="true" className={fetching ? "animate-spin" : ""} />
        </Button>
      </header>

      <WorkingStatusBar projection={projection} />
      <div className="mx-auto grid w-full min-w-0 max-w-5xl grid-cols-[minmax(0,1fr)] gap-4 p-4 sm:p-6">
        {detail.truncated_fields.length ? (
          <p className="rounded-lg bg-warning/10 p-3 text-sm text-warning" role="status">
            为避免页面过大，{detail.truncated_fields.length} 个字段已由后端截断。
          </p>
        ) : null}
        {cancelError ? (
          <p className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive" role="alert">
            {cancelError instanceof Error ? cancelError.message : "停止请求提交失败。"}
          </p>
        ) : null}

        <section aria-label="运行概览" className="grid grid-cols-2 gap-2 md:grid-cols-3">
          <OverviewItem label="会话" value={detail.session_id} />
          <OverviewItem label="执行来源" value={executionSourceLabel(detail.execution_source)} />
          <OverviewItem label="模型" value={detail.model_id || "暂无数据"} />
          <OverviewItem label="耗时" value={formatDuration(detail.duration_ms)} />
          <OverviewItem label="输入 Token" value={formatToken(detail.usage.input_tokens)} />
          <OverviewItem label="输出 Token" value={formatToken(detail.usage.output_tokens)} />
          <OverviewItem label="缓存读取 Token" value={formatToken(detail.usage.cache_read_tokens)} />
          <OverviewItem label="缓存未命中 Token" value={formatToken(detail.usage.cache_miss_tokens)} />
          <OverviewItem label="缓存写入 Token" value={formatToken(detail.usage.cache_write_tokens)} />
          <OverviewItem label="推理 Token" value={formatToken(detail.usage.reasoning_tokens)} />
        </section>

        <section className="run-detail-section">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2>任务与回答</h2>
            <time className="flex items-center gap-1 text-xs text-muted-foreground">
              <Clock3 aria-hidden="true" className="size-3.5" />
              {formatRunDate(detail.finished_at || detail.started_at)}
            </time>
          </div>
          <h3 className="mt-4 text-xs font-semibold text-muted-foreground">用户输入</h3>
          <p className="mt-1 whitespace-pre-wrap text-sm">{detail.query || "当时未保存"}</p>
          <h3 className="mt-5 flex items-center gap-2 text-xs font-semibold text-muted-foreground">
            <Bot aria-hidden="true" className="size-4" />
            最终回答
          </h3>
          <div className="mt-2">
            {detail.response_text ? (
              <Suspense fallback={<div className="h-16 animate-pulse rounded-lg bg-muted" />}>
                <CitationMessageResponse citationBundle={detail.citation_bundle ?? null}>
                  {detail.response_text}
                </CitationMessageResponse>
              </Suspense>
            ) : (
              <p className="text-sm text-muted-foreground">当时未保存完整回答。</p>
            )}
          </div>
          <Link
            className="mt-4 inline-flex min-h-11 items-center gap-2 text-sm font-medium text-primary hover:underline"
            to={`/agent/${encodeURIComponent(detail.session_id)}`}
          >
            打开所属会话
            <ExternalLink aria-hidden="true" className="size-4" />
          </Link>
        </section>

        {trace.length ? (
          <ExecutionTimeline active={ACTIVE_STATUSES.has(detail.status)} items={trace} />
        ) : null}
        <WorkingStateDetails state={detail.working_state} />
        <PlanSection plan={detail.plan} />
        <RunArtifactsSection artifacts={detail.artifacts ?? []} />
        <ObservationsSection items={detail.observations} />
        <ModelRequestsSection items={detail.model_requests} />
        <RunErrorSection detail={detail} />
        <PromptSnapshotSection snapshot={detail.prompt_snapshot} />
      </div>
    </article>
  );
}

function OverviewItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border bg-muted/30 p-3">
      <span className="block text-xs text-muted-foreground">{label}</span>
      <strong className="mt-1 block truncate text-sm font-medium" title={value}>{value}</strong>
    </div>
  );
}

function RunDetailEmpty() {
  return (
    <div className="grid h-full place-items-center p-6 text-center">
      <div>
        <strong>请选择一次运行</strong>
        <p className="mt-1 text-sm text-muted-foreground">
          这里会展示回答、WorkingState、执行过程、模型请求、Evidence 和错误信息。
        </p>
      </div>
    </div>
  );
}

function RunDetailLoading({ runId }: { runId: string }) {
  return (
    <div aria-label="正在加载运行详情" className="grid h-full place-items-center p-6" role="status">
      <div className="text-center">
        <LoaderCircle aria-hidden="true" className="mx-auto size-6 animate-spin text-primary" />
        <strong className="mt-3 block">正在读取运行详情</strong>
        <code className="mt-1 block text-xs text-muted-foreground">{runId}</code>
      </div>
    </div>
  );
}
