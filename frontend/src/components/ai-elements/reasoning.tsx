"use client";

import { Collapsible } from "@base-ui/react/collapsible";
import { Brain, ChevronDown, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState, type ComponentProps } from "react";

import { cn } from "@/lib/utils";

export type ReasoningProps = Omit<
  ComponentProps<typeof Collapsible.Root>,
  "defaultOpen" | "onOpenChange" | "open"
> & {
  isStreaming?: boolean;
};

export function Reasoning({
  className,
  isStreaming = false,
  ...props
}: ReasoningProps) {
  const [open, setOpen] = useState(isStreaming);
  const previousStreaming = useRef(isStreaming);

  useEffect(() => {
    if (previousStreaming.current !== isStreaming) {
      setOpen(isStreaming);
      previousStreaming.current = isStreaming;
    }
  }, [isStreaming]);

  return (
    <Collapsible.Root
      className={cn("group/reasoning", className)}
      data-streaming={isStreaming || undefined}
      onOpenChange={setOpen}
      open={open}
      {...props}
    />
  );
}

export type ReasoningTriggerProps = ComponentProps<typeof Collapsible.Trigger> & {
  isStreaming?: boolean;
  summary: string;
};

export function ReasoningTrigger({
  className,
  isStreaming = false,
  summary,
  ...props
}: ReasoningTriggerProps) {
  const Icon = isStreaming ? LoaderCircle : Brain;
  return (
    <Collapsible.Trigger
      className={cn(
        "flex min-h-11 w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-muted-foreground",
        "outline-none transition-colors hover:bg-muted/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      {...props}
    >
      <Icon
        aria-hidden="true"
        className={cn("size-4 shrink-0", isStreaming && "animate-spin text-primary")}
      />
      <span className="min-w-0 flex-1 truncate">{summary}</span>
      <ChevronDown
        aria-hidden="true"
        className="size-4 shrink-0 transition-transform group-data-[open]/reasoning:rotate-180"
      />
    </Collapsible.Trigger>
  );
}

export type ReasoningContentProps = ComponentProps<typeof Collapsible.Panel>;

export function ReasoningContent({
  className,
  ...props
}: ReasoningContentProps) {
  return (
    <Collapsible.Panel
      className={cn(
        "mx-3 mb-2 border-l-2 pl-3 text-xs leading-5 text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}
