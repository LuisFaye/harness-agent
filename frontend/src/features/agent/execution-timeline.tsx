import { Collapsible } from "@base-ui/react/collapsible";
import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { ShimmerText } from "@/components/ui/shimmer-text";
import type { TraceItem } from "@/lib/stream/schemas";
import { cn } from "@/lib/utils";

import {
  buildExecutionViewModels,
  isStepActive,
  toolNarrative,
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
  defaultOpen = false,
}: ExecutionTimelineProps) {
  const allViewModels = buildExecutionViewModels(items, active)
    .filter((item) => !(item.kind === "tool" && ["final", "citations"].includes(item.tool.key)));
  const hasSubstantiveSteps = allViewModels.some(
    (item) => item.kind === "reasoning" || item.kind === "tool",
  );
  const viewModels = hasSubstantiveSteps
    ? allViewModels.filter((item) => item.kind !== "narrative")
    : allViewModels;

  const isLiveStep = active && !answerStreaming;
  const [open, setOpen] = useState<boolean>(defaultOpen);

  if (!allViewModels.length && !isLiveStep) {
    return null;
  }

  const summaryText = buildTimelineSummary(allViewModels, active, answerStreaming);
  const liveSnippet = isLiveStep && !open ? extractLiveThoughtSnippet(allViewModels) : "";
  const hasError = viewModels.some((item) => item.kind === "tool" && item.tool.state === "error");
  const canExpand = viewModels.length > 0;

  if (!canExpand) {
    return (
      <section aria-label="执行过程" className="execution-progress mb-1.5">
        <div
          aria-label={`思考过程：${summaryText}`}
          className="-ml-1 inline-flex max-w-full items-center gap-2 px-1 py-0.5 text-xs font-medium text-muted-foreground"
          role="status"
        >
          <span aria-hidden="true" className="execution-orb" />
          <span className="execution-ticker-text truncate" key={summaryText}>
            <ShimmerText>{summaryText}</ShimmerText>
          </span>
        </div>
      </section>
    );
  }

  return (
    <section aria-label="执行过程" className="execution-progress mb-1.5">
      <Collapsible.Root
        className="group/timeline"
        onOpenChange={setOpen}
        open={open}
      >
        <Collapsible.Trigger
          aria-label={`思考过程：${summaryText}`}
          className={cn(
            "-ml-1.5 inline-flex max-w-full items-center gap-2 rounded-lg px-1.5 py-0.5 text-left text-xs font-medium transition-colors outline-none",
            "text-muted-foreground/90 hover:bg-muted/50 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <span
            aria-hidden="true"
            className={cn(
              isLiveStep
                ? "execution-orb"
                : cn(
                    "size-1.5 shrink-0 rounded-full transition-colors",
                    hasError ? "bg-destructive/80" : "bg-muted-foreground/45",
                  ),
            )}
          />
          <span className="execution-ticker-text shrink-0" key={summaryText}>
            {isLiveStep ? (
              <ShimmerText>{summaryText}</ShimmerText>
            ) : (
              <span>{summaryText}</span>
            )}
          </span>
          {liveSnippet ? (
            <span className="execution-ticker-text hidden max-w-[260px] truncate font-normal text-muted-foreground/55 sm:inline">
              · {liveSnippet}
            </span>
          ) : null}
          <ChevronRight
            aria-hidden="true"
            className="size-3.5 shrink-0 opacity-45 transition-transform duration-200 ease-out group-hover/timeline:translate-x-0.5 group-hover/timeline:opacity-75 group-data-[open]/timeline:translate-x-0 group-data-[open]/timeline:rotate-90"
          />
        </Collapsible.Trigger>

        <Collapsible.Panel className="execution-panel mt-1.5 ml-1">
          <div
            aria-live={active ? "polite" : undefined}
            className="execution-thread space-y-2 pl-3.5 py-0.5"
          >
            {viewModels.map((item) => (
              <ExecutionStreamStep item={item} key={streamKey(item)} />
            ))}
          </div>
        </Collapsible.Panel>
      </Collapsible.Root>
    </section>
  );
}

function buildTimelineSummary(
  viewModels: ExecutionViewModel[],
  active: boolean,
  answerStreaming: boolean,
): string {
  if (active && !answerStreaming) {
    const activeItem = [...viewModels].reverse().find(isStepActive);
    if (activeItem) {
      if (activeItem.kind === "reasoning") {
        return activeItem.reasoning.summary;
      }
      if (activeItem.kind === "tool") {
        return `正在${toolNarrative(activeItem.tool)}`;
      }
      return activeItem.narrative.text;
    }
    return viewModels.length ? "正在整理信息…" : "正在思考…";
  }

  const toolCount = viewModels.filter((item) => item.kind === "tool").length;
  const hasReasoning = viewModels.some((item) => item.kind === "reasoning");
  if (hasReasoning && toolCount > 0) {
    return `已思考 · ${toolCount} 步`;
  }
  if (toolCount > 0) {
    return `已完成 ${toolCount} 步`;
  }
  return "已思考";
}

function extractLiveThoughtSnippet(viewModels: ExecutionViewModel[]): string {
  for (let i = viewModels.length - 1; i >= 0; i -= 1) {
    const item = viewModels[i];
    if (item?.kind === "reasoning" && item.reasoning.content) {
      const lines = item.reasoning.content
        .split(/\r?\n/)
        .map((line) => line.replace(/^[#>*-\s]+/, "").trim())
        .filter(Boolean);
      const lastLine = lines.at(-1);
      if (lastLine) {
        return lastLine.slice(0, 48);
      }
    }
  }
  return "";
}

function ExecutionStreamStep({ item }: { item: ExecutionViewModel }) {
  if (item.kind === "reasoning") {
    return <ReasoningStreamStep item={item} />;
  }
  if (item.kind === "narrative") {
    return (
      <p className="relative text-[12.5px] leading-relaxed text-muted-foreground/85">
        <span aria-hidden="true" className="execution-node" />
        {item.narrative.isStreaming ? (
          <ShimmerText>{item.narrative.text}</ShimmerText>
        ) : (
          item.narrative.text
        )}
      </p>
    );
  }
  return <ToolStreamStep item={item.tool} />;
}

function ReasoningStreamStep({
  item,
}: {
  item: Extract<ExecutionViewModel, { kind: "reasoning" }>;
}) {
  const reasoning = item.reasoning;
  const content = reasoning.content || "暂无可公开的分析摘要。";

  return (
    <div className="relative text-[12.5px] leading-[1.68] text-muted-foreground/85">
      <span
        aria-hidden="true"
        className={cn(
          "execution-node",
          reasoning.isStreaming && "execution-node-active",
        )}
      />
      <p className="whitespace-pre-wrap">{content}</p>
    </div>
  );
}

function ToolStreamStep({ item }: { item: ToolCallViewModel }) {
  const active = item.state === "running";
  const label = toolNarrative(item);
  const errorText = item.state === "error" && item.error
    ? String(item.error)
    : null;

  return (
    <div
      className={cn(
        "relative flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[12.5px] leading-relaxed",
        item.state === "error" ? "text-destructive/90" : "text-muted-foreground/85",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "execution-node",
          active && "execution-node-active",
          item.state === "error" && "execution-node-error",
        )}
      />
      {active ? (
        <ShimmerText className="font-medium">{label}</ShimmerText>
      ) : (
        <span>{label}</span>
      )}
      {errorText ? (
        <span className="text-xs text-destructive/75">· {errorText}</span>
      ) : null}
    </div>
  );
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
