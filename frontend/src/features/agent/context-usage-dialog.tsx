import { DatabaseZap, Gauge, Layers3 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { RunUsage } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

interface SectionUsage {
  key?: string;
  label?: string;
  estimated_tokens?: number;
  injected?: boolean;
}

interface ContextBudget {
  limit_value?: number | null;
  used_value?: number | null;
  total_value?: number | null;
  truncated?: boolean;
  dropped_sections?: string[] | null;
  section_usage?: SectionUsage[] | null;
}

interface ContextUsageDialogProps {
  contextObservation: Record<string, unknown> | null;
  usage: RunUsage | null;
}

export function ContextUsageDialog({
  contextObservation,
  usage,
}: ContextUsageDialogProps) {
  const rawBudget = contextObservation?.budget;
  const budget = rawBudget && typeof rawBudget === "object" && !Array.isArray(rawBudget)
    ? (rawBudget as ContextBudget)
    : null;
  const limitValue = toFiniteNumber(budget?.limit_value);
  const usedValue = toFiniteNumber(budget?.used_value);
  const totalValue = toFiniteNumber(budget?.total_value);
  const hasBudgetData = limitValue !== null && usedValue !== null;
  const percent = limitValue !== null && limitValue > 0 && usedValue !== null
    ? clampPercentage((usedValue / limitValue) * 100)
    : null;
  const remainingValue = limitValue !== null && usedValue !== null
    ? Math.max(0, limitValue - usedValue)
    : null;
  const sections = Array.isArray(budget?.section_usage)
    ? budget.section_usage.filter((item) => item && typeof item === "object")
    : [];
  const triggerLabel = usedValue === null || limitValue === null
    ? "上下文 --"
    : `${formatCompactTokens(usedValue)} / ${formatCompactTokens(limitValue)}`;
  const triggerAriaLabel = usedValue === null || limitValue === null
    ? "查看当前会话上下文详情，暂无用量数据"
    : `查看当前会话上下文详情，已用 ${formatInteger(usedValue)}，上限 ${formatInteger(limitValue)} Token`;

  return (
    <Dialog>
      <DialogTrigger
        render={
          <Button
            aria-label={triggerAriaLabel}
            className="h-11 rounded-full px-3 text-xs font-semibold"
            title="上下文详情"
            variant="ghost"
          >
            <ContextRing percent={percent} />
            <span className="tabular-nums">{triggerLabel}</span>
          </Button>
        }
      />
      <DialogContent className="max-h-[calc(100svh-2rem)] gap-4 overflow-y-auto p-4 sm:max-w-lg sm:p-5">
        <DialogHeader className="pr-8">
          <DialogTitle className="text-lg">上下文概览</DialogTitle>
          <DialogDescription>
            查看本次注入的用户上下文，以及最近一次模型运行上报的 Token 与缓存数据。
          </DialogDescription>
        </DialogHeader>

        <section
          aria-label="注入上下文预算"
          className="rounded-xl border border-primary/15 bg-primary/5 p-4"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <Gauge aria-hidden="true" className="size-4 text-primary" />
                本次注入
              </div>
              <p className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <strong className="text-2xl font-semibold tracking-tight tabular-nums text-foreground">
                  {usedValue === null ? "--" : formatInteger(usedValue)}
                </strong>
                <span className="text-sm tabular-nums text-muted-foreground">
                  / {limitValue === null ? "--" : formatInteger(limitValue)} Token
                </span>
              </p>
            </div>
            <span
              className={cn(
                "shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium",
                !hasBudgetData
                  ? "border-border bg-muted text-muted-foreground"
                  : budget?.truncated
                    ? "border-warning/30 bg-card text-warning"
                    : "border-success/30 bg-card text-success",
              )}
            >
              {!hasBudgetData ? "等待数据" : budget?.truncated ? "已达上限" : "完整注入"}
            </span>
          </div>

          <div className="mt-4">
            <div
              aria-label="上下文预算占用"
              aria-valuemax={limitValue ?? undefined}
              aria-valuemin={0}
              aria-valuenow={usedValue ?? undefined}
              className="h-2 overflow-hidden rounded-full bg-primary/10"
              role="progressbar"
            >
              <div
                className={cn(
                  "h-full rounded-full transition-[width] duration-300 motion-reduce:transition-none",
                  progressToneClass(percent),
                )}
                style={{ width: progressWidth(percent) }}
              />
            </div>
            <div className="mt-2 flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>{percent === null ? "占用比例未上报" : `已用 ${formatPercentage(percent)}`}</span>
              <span>
                {remainingValue === null
                  ? "剩余额度未上报"
                  : `剩余 ${formatInteger(remainingValue)} Token`}
              </span>
            </div>
          </div>

          {budget?.truncated ? (
            <p className="mt-3 rounded-lg border border-warning/20 bg-warning/10 px-3 py-2 text-xs text-foreground">
              候选上下文约 {formatInteger(totalValue ?? usedValue ?? 0)} Token，超出上限的内容没有注入本次请求。
            </p>
          ) : null}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            这里统计用户偏好、记忆、会话摘要和最近对话，不包含系统规则、工具定义、附件、模型输出预留等整次请求开销。
          </p>
        </section>

        <section aria-label="上下文组成" className="rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2">
            <Layers3 aria-hidden="true" className="size-4 text-primary" />
            <h3 className="text-sm font-semibold">上下文组成</h3>
          </div>
          {sections.length > 0 ? (
            <ul className="mt-3 space-y-3">
              {sections.map((section, index) => {
                const sectionTokens = toFiniteNumber(section.estimated_tokens) ?? 0;
                const sectionPercent = usedValue !== null && usedValue > 0
                  ? clampPercentage((sectionTokens / usedValue) * 100)
                  : 0;
                return (
                  <li key={`${String(section.key ?? section.label ?? "section")}-${index}`}>
                    <div className="flex items-center justify-between gap-3 text-sm">
                      <span className="min-w-0 truncate text-foreground">
                        {section.label || "未命名分类"}
                        {section.injected === false ? (
                          <span className="ml-2 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                            未注入
                          </span>
                        ) : null}
                      </span>
                      <span className="shrink-0 tabular-nums text-muted-foreground">
                        {formatInteger(sectionTokens)}
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className={cn(
                          "h-full rounded-full",
                          section.injected === false ? "bg-muted-foreground/30" : "bg-primary/70",
                        )}
                        style={{ width: progressWidth(sectionPercent) }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-muted-foreground">
              暂无组成数据，运行一次任务后会显示各类上下文的 Token 估算。
            </p>
          )}
        </section>

        <section aria-label="最近一次模型运行" className="rounded-xl border bg-card p-4">
          <div className="flex items-center gap-2">
            <DatabaseZap aria-hidden="true" className="size-4 text-primary" />
            <h3 className="text-sm font-semibold">最近一次模型运行</h3>
          </div>
          {usage ? (
            <>
              <dl className="mt-3 grid grid-cols-1 gap-2 min-[420px]:grid-cols-3">
                <UsageMetric label="输入" value={formatReportedToken(usage.input_tokens)} />
                <UsageMetric label="输出" value={formatReportedToken(usage.output_tokens)} />
                <UsageMetric label="缓存命中率" value={formatCacheHitRate(usage)} />
              </dl>
              <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-3 border-t pt-3">
                <UsageItem label="缓存读取/命中" value={formatReportedToken(usage.cache_read_tokens)} />
                <UsageItem label="缓存未命中" value={formatReportedToken(usage.cache_miss_tokens)} />
                <UsageItem label="缓存写入" value={formatReportedToken(usage.cache_write_tokens)} />
                <UsageItem label="推理" value={formatReportedToken(usage.reasoning_tokens)} />
              </dl>
              <p className="mt-3 text-xs leading-5 text-muted-foreground">
                缓存命中率优先按读取 /（读取 + 未命中）计算；供应商未返回未命中时，回退为读取 / 输入。
              </p>
            </>
          ) : (
            <p className="mt-3 text-sm text-muted-foreground">
              当前会话还没有模型运行记录，完成一次任务后会显示供应商上报的用量。
            </p>
          )}
        </section>

        <p className="px-1 text-xs leading-5 text-muted-foreground">
          上下文组成为本地 Token 估算；模型用量与缓存指标来自供应商上报，未返回的字段显示“未上报”。
        </p>
      </DialogContent>
    </Dialog>
  );
}

function ContextRing({ percent }: { percent: number | null }) {
  const radius = 8;
  const circumference = 2 * Math.PI * radius;
  const progress = percent === null ? 0 : Math.max(0, Math.min(1, percent / 100));
  const strokeDashoffset = circumference * (1 - progress);
  const toneClass = percent === null
    ? "stroke-border"
    : percent < 70
      ? "stroke-primary"
      : percent < 90
        ? "stroke-warning"
        : "stroke-destructive";
  return (
    <svg
      aria-hidden="true"
      className="size-5 shrink-0 [&>circle]:fill-none"
      viewBox="0 0 20 20"
    >
      <circle
        className="stroke-border"
        cx="10"
        cy="10"
        r={radius}
        strokeWidth="2.5"
      />
      <circle
        className={cn("transition-[stroke-dashoffset] duration-300 motion-reduce:transition-none", toneClass)}
        cx="10"
        cy="10"
        r={radius}
        strokeDasharray={circumference}
        strokeDashoffset={strokeDashoffset}
        strokeLinecap="round"
        strokeWidth="2.5"
        transform="rotate(-90 10 10)"
      />
    </svg>
  );
}

function UsageMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-muted/55 px-3 py-2.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 truncate text-sm font-semibold tabular-nums text-foreground">{value}</dd>
    </div>
  );
}

function UsageItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-sm tabular-nums text-foreground">{value}</dd>
    </div>
  );
}

function formatCacheHitRate(usage: RunUsage): string {
  const hitTokens = usage.cache_read_tokens;
  const missTokens = usage.cache_miss_tokens;
  const hasHit = hitTokens !== null && hitTokens !== undefined;
  const hasMiss = missTokens !== null && missTokens !== undefined;
  if (!hasHit) {
    return "未上报";
  }
  if (hasMiss) {
    const total = hitTokens + missTokens;
    if (total <= 0) {
      return "0%";
    }
    return formatPercentage(clampPercentage((hitTokens / total) * 100));
  }
  if (usage.input_tokens === null || usage.input_tokens === undefined || usage.input_tokens <= 0) {
    return "未上报";
  }
  return formatPercentage(clampPercentage((hitTokens / usage.input_tokens) * 100));
}

function formatReportedToken(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "未上报"
    : `${formatInteger(value)} Token`;
}

function formatInteger(value: number): string {
  return Math.round(value).toLocaleString("zh-CN");
}

function formatCompactTokens(value: number): string {
  const rounded = Math.round(value);
  if (Math.abs(rounded) >= 1_000_000) {
    return `${formatCompactNumber(rounded / 1_000_000)}M`;
  }
  if (Math.abs(rounded) >= 1_000) {
    return `${formatCompactNumber(rounded / 1_000)}K`;
  }
  return rounded.toLocaleString("zh-CN");
}

function formatCompactNumber(value: number): string {
  return value.toLocaleString("zh-CN", {
    maximumFractionDigits: value >= 100 ? 0 : value >= 10 ? 1 : 2,
  });
}

function formatPercentage(value: number): string {
  if (value > 0 && value < 1) {
    return "<1%";
  }
  return `${Math.round(value)}%`;
}

function progressToneClass(percent: number | null): string {
  if (percent === null || percent < 70) {
    return "bg-primary";
  }
  if (percent < 90) {
    return "bg-warning";
  }
  return "bg-destructive";
}

function progressWidth(percent: number | null): string {
  if (percent === null || percent <= 0) {
    return "0%";
  }
  return `${Math.max(1, Math.min(100, percent))}%`;
}

function toFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clampPercentage(value: number): number {
  return Math.max(0, Math.min(100, value));
}
