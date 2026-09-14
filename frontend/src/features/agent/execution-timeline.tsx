import { Collapsible } from "@base-ui/react/collapsible";
import { ChevronDown, Diamond } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { ShimmerText } from "@/components/ui/shimmer-text";
import type { TraceItem } from "@/lib/stream/schemas";
import { cn } from "@/lib/utils";

import {
  buildExecutionViewModels,
  isStepActive,
  toolNarrative,
  toolStatusLabel,
  type ExecutionViewModel,
  type ToolCallViewModel,
} from "./execution-view-model";

interface ExecutionTimelineProps {
  active: boolean;
  answerStreaming?: boolean;
  items: TraceItem[];
  defaultOpen?: boolean;
  startedAt?: string | null;
  updatedAt?: string | null;
}

export function ExecutionTimeline({
  active,
  answerStreaming = false,
  items,
}: ExecutionTimelineProps) {
  const viewModels = buildExecutionViewModels(items, active)
    .filter((item) => !(item.kind === "tool" && ["final", "citations"].includes(item.tool.key)));
  const hasActiveStep = viewModels.some(isStepActive);

  if (!viewModels.length && (!active || answerStreaming)) {
    return null;
  }

  return (
    <section aria-label="执行过程" className="execution-progress mb-3">
      <div aria-live={active ? "polite" : undefined} className="space-y-2">
        {viewModels.map((item) => (
          <ExecutionStreamStep item={item} key={streamKey(item)} />
        ))}
        {active && !answerStreaming && !hasActiveStep ? (
          <p className="py-0.5 text-sm" role="status">
            <ShimmerText>{viewModels.length ? "正在整理已有信息…" : "正在分析需求…"}</ShimmerText>
          </p>
        ) : null}
      </div>
    </section>
  );
}

function ExecutionStreamStep({ item }: { item: ExecutionViewModel }) {
  if (item.kind === "reasoning") {
    return <ReasoningStreamStep item={item} />;
  }
  if (item.kind === "narrative") {
    return <NarrativeStreamStep item={item} />;
  }
  return <ToolStreamStep item={item.tool} />;
}

function ReasoningStreamStep({
  item,
}: {
  item: Extract<ExecutionViewModel, { kind: "reasoning" }>;
}) {
  const reasoning = item.reasoning;
  const active = reasoning.isStreaming;
  const userToggledRef = useRef(false);
  const [open, setOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const content = reasoning.content || "暂无可公开的分析摘要。";

  useEffect(() => {
    if (userToggledRef.current) {
      return;
    }
    if (!active) setOpen(false);
  }, [active]);

  useEffect(() => {
    if (!active || !open || !contentRef.current) {
      return;
    }
    contentRef.current.scrollTop = contentRef.current.scrollHeight;
  }, [active, content, open]);

  return (
    <Collapsible.Root
      className="group/reasoning"
      onOpenChange={(next) => {
        userToggledRef.current = true;
        setOpen(next);
      }}
      open={open}
    >
      <Collapsible.Trigger
        className="flex items-center gap-1.5 rounded-sm py-0.5 text-left text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        {active ? (
          <ShimmerText>{reasoning.summary}</ShimmerText>
        ) : (
          <span>{reasoning.summary}</span>
        )}
        <ChevronDown
          aria-hidden="true"
          className="size-3.5 shrink-0 opacity-60 transition-transform group-data-[open]/reasoning:rotate-180"
        />
      </Collapsible.Trigger>
      <Collapsible.Panel className="mt-1 border-l border-border/60 pl-3 text-sm leading-relaxed text-muted-foreground/90">
        <div className="max-h-48 overflow-y-auto" ref={contentRef}>
          <p className="whitespace-pre-wrap">{content}</p>
          {reasoning.isTruncated ? (
            <p className="mt-1 text-xs text-muted-foreground/80">思考内容较长，已截断展示。</p>
          ) : null}
        </div>
      </Collapsible.Panel>
    </Collapsible.Root>
  );
}

function NarrativeStreamStep({
  item,
}: {
  item: Extract<ExecutionViewModel, { kind: "narrative" }>;
}) {
  const narrative = item.narrative;
  return (
    <p className="py-1 text-[15px] leading-7 text-foreground/90">
      {narrative.isStreaming ? (
        <ShimmerText>{narrative.text}</ShimmerText>
      ) : (
        narrative.text
      )}
    </p>
  );
}

function ToolStreamStep({ item }: { item: ToolCallViewModel }) {
  const [detailOpen, setDetailOpen] = useState(false);
  const active = item.state === "running";
  const status = toolStatusLabel(item);
  const label = toolNarrative(item);
  const hasDetail = item.input !== null
    || item.output !== null
    || item.error !== null;

  return (
    <div>
      <button
        aria-expanded={detailOpen}
        className={cn(
          "flex w-full items-center gap-2 rounded-sm py-0.5 text-left text-sm outline-none",
          "hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring",
          item.state === "error" ? "text-destructive/90" : "text-muted-foreground",
        )}
        onClick={() => setDetailOpen((current) => !current)}
        type="button"
      >
        <Diamond aria-hidden="true" className="size-3 shrink-0 opacity-70" />
        {active ? (
          <ShimmerText className="min-w-0 flex-1 truncate text-[13px]">
            {label}
          </ShimmerText>
        ) : (
          <span className="min-w-0 flex-1 truncate text-[13px] text-foreground/80">
            {label}
          </span>
        )}
        <span className="shrink-0 text-xs">{status}</span>
        {item.durationMs !== null ? (
          <span className="shrink-0 text-xs tabular-nums opacity-80">
            ({formatDuration(item.durationMs)})
          </span>
        ) : null}
      </button>
      {detailOpen && hasDetail ? (
        <div className="mb-1 ml-5 space-y-1 rounded-md bg-muted/25 px-2.5 py-2 text-xs text-muted-foreground">
          <DetailRow label="工具">
            <span>{item.name || label}</span>
          </DetailRow>
          <DetailRow label="输入">
            <DetailValue emptyText="本步骤没有结构化输入" value={item.input} />
          </DetailRow>
          {item.error !== null && item.error !== undefined && item.error !== "" ? (
            <DetailRow label="错误">
              <DetailValue emptyText="没有额外错误信息" value={item.error} />
            </DetailRow>
          ) : null}
          <DetailRow label="结果">
            <DetailValue
              emptyText={item.state === "running" ? "正在等待结果" : "没有额外结果摘要"}
              value={item.output}
            />
          </DetailRow>
        </div>
      ) : null}
    </div>
  );
}

function DetailRow({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[3.5rem_minmax(0,1fr)] sm:gap-2">
      <span>{label}</span>
      <div className="min-w-0 break-words text-foreground/90">{children}</div>
    </div>
  );
}

function DetailValue({
  emptyText,
  value,
}: {
  emptyText: string;
  value: unknown;
}) {
  if (value === null || value === undefined || value === "") {
    return <span>{emptyText}</span>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) {
      return <span>{emptyText}</span>;
    }
    return (
      <dl className="space-y-0.5">
        {entries.map(([key, entryValue]) => (
          <div className="grid grid-cols-[minmax(3rem,auto)_1fr] gap-1.5" key={key}>
            <dt>{key}</dt>
            <dd className="min-w-0 break-words">{renderValue(entryValue)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return <>{String(value)}</>;
}

function renderValue(value: unknown): ReactNode {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "object") {
    if (Array.isArray(value)) {
      return (
        <ul className="space-y-0.5">
          {value.map((entry, index) => <li key={index}>{renderValue(entry)}</li>)}
        </ul>
      );
    }
    return (
      <dl className="space-y-0.5">
        {Object.entries(value as Record<string, unknown>).map(([key, entry]) => (
          <div className="grid grid-cols-[minmax(3rem,auto)_1fr] gap-1.5" key={key}>
            <dt>{key}</dt>
            <dd className="min-w-0 break-words">{renderValue(entry)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return String(value);
}

function streamKey(item: ExecutionViewModel): string {
  if (item.kind === "reasoning") {
    return item.reasoning.key;
  }
  if (item.kind === "narrative") {
    return item.narrative.key;
  }
  return item.tool.key;
}

function formatDuration(durationMs: number): string {
  return durationMs < 1000 ? `${durationMs}ms` : `${(durationMs / 1000).toFixed(1)}s`;
}
