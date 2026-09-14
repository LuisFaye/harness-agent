"use client";

import { Collapsible } from "@base-ui/react/collapsible";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  LoaderCircle,
  Wrench,
} from "lucide-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

export type ToolState =
  | "pending"
  | "running"
  | "completed"
  | "blocked"
  | "error";

export type ToolProps = ComponentProps<typeof Collapsible.Root>;

export function Tool({
  className,
  ...props
}: ToolProps) {
  return (
    <Collapsible.Root
      className={cn(
        "group/tool overflow-hidden rounded-lg border bg-card text-card-foreground",
        className,
      )}
      {...props}
    />
  );
}

interface ToolHeaderProps extends ComponentProps<typeof Collapsible.Trigger> {
  kind: string;
  source?: string | null;
  state: ToolState;
  summary?: string;
  title: string;
  meta?: string;
}

export function ToolHeader({
  className,
  kind,
  meta,
  source,
  state,
  summary,
  title,
  ...props
}: ToolHeaderProps) {
  const Icon = stateIcon(state);
  return (
    <Collapsible.Trigger
      className={cn(
        "flex min-h-11 w-full items-center gap-2 px-3 py-2 text-left outline-none transition-colors",
        "hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        className,
      )}
      {...props}
    >
      <Icon
        aria-label={stateLabel(state)}
        className={cn(
          "size-4 shrink-0 text-muted-foreground",
          state === "running" && "animate-spin text-primary",
          state === "completed" && "text-success",
          ["blocked", "error"].includes(state) && "text-warning",
        )}
      />
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-baseline gap-2">
          <strong className="truncate text-sm font-medium">{title}</strong>
          {meta ? (
            <span className="shrink-0 text-xs text-muted-foreground">{meta}</span>
          ) : null}
        </span>
        <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          {source ? <span className="shrink-0">{source}</span> : null}
          {source && summary ? <span aria-hidden="true">·</span> : null}
          {summary ? <span className="truncate">{summary}</span> : null}
        </span>
      </span>
      <span className={cn(
        "shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium",
        kind === "MCP" && "bg-primary-soft text-accent-foreground",
        kind === "Skill" && "bg-secondary text-secondary-foreground",
        kind === "Sub-Agent" && "bg-warning/10 text-warning",
        kind === "Tool" && "bg-secondary text-secondary-foreground",
      )}>
        {kind}
      </span>
      <ChevronDown
        aria-hidden="true"
        className="size-4 shrink-0 text-muted-foreground transition-transform group-data-[open]/tool:rotate-180"
      />
    </Collapsible.Trigger>
  );
}

export type ToolContentProps = ComponentProps<typeof Collapsible.Panel>;

export function ToolContent({
  className,
  ...props
}: ToolContentProps) {
  return (
    <Collapsible.Panel
      className={cn(
        "border-t bg-muted/20 px-3 py-3 text-sm",
        "data-ending-style:h-0 data-starting-style:h-0",
        className,
      )}
      {...props}
    />
  );
}

interface ToolDetailProps extends ComponentProps<"div"> {
  label: string;
  children: ReactNode;
}

export function ToolDetail({
  children,
  className,
  label,
  ...props
}: ToolDetailProps) {
  return (
    <div
      className={cn("grid gap-1 sm:grid-cols-[5rem_minmax(0,1fr)] sm:gap-3", className)}
      {...props}
    >
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <div className="min-w-0 break-words text-xs leading-5 text-foreground">
        {children}
      </div>
    </div>
  );
}

export function ToolName({
  children,
  className,
  ...props
}: ComponentProps<"code">) {
  return (
    <code
      className={cn("rounded bg-muted px-1.5 py-0.5 font-mono text-xs", className)}
      {...props}
    >
      {children}
    </code>
  );
}

export function ToolEmptyDetail({ children }: { children: ReactNode }) {
  return (
    <span className="text-muted-foreground">
      {children}
    </span>
  );
}

function stateIcon(state: ToolState) {
  if (state === "completed") {
    return CheckCircle2;
  }
  if (state === "running") {
    return LoaderCircle;
  }
  if (state === "error") {
    return AlertCircle;
  }
  if (state === "blocked") {
    return Clock3;
  }
  if (state === "pending") {
    return Circle;
  }
  return Wrench;
}

function stateLabel(state: ToolState): string {
  return {
    blocked: "等待处理",
    completed: "执行完成",
    error: "执行失败",
    pending: "等待执行",
    running: "正在执行",
  }[state];
}
