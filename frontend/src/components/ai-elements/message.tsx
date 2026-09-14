"use client";

import type { ComponentProps } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type MessageProps = ComponentProps<"article"> & {
  from: "user" | "assistant";
};

export function Message({
  className,
  from,
  ...props
}: MessageProps) {
  return (
    <article
      className={cn(
        "group flex w-full flex-col gap-2",
        from === "user" ? "is-user ml-auto max-w-[85%] items-end sm:max-w-[72%]" : "is-assistant",
        className,
      )}
      data-role={from}
      {...props}
    />
  );
}

export type MessageContentProps = ComponentProps<"div">;

export function MessageContent({
  className,
  ...props
}: MessageContentProps) {
  return (
    <div
      className={cn(
        "min-w-0 max-w-full text-sm leading-6",
        "group-[.is-user]:rounded-2xl group-[.is-user]:border group-[.is-user]:border-primary/15 group-[.is-user]:bg-primary-soft group-[.is-user]:px-4 group-[.is-user]:py-3",
        "group-[.is-assistant]:w-full group-[.is-assistant]:text-foreground",
        className,
      )}
      {...props}
    />
  );
}

export type MessageActionsProps = ComponentProps<"div">;

export function MessageActions({
  className,
  ...props
}: MessageActionsProps) {
  return (
    <div
      className={cn("flex min-h-11 items-center gap-1", className)}
      {...props}
    />
  );
}

export type MessageActionProps = ComponentProps<typeof Button> & {
  label: string;
};

export function MessageAction({
  children,
  label,
  ...props
}: MessageActionProps) {
  return (
    <Button
      aria-label={label}
      size="icon-sm"
      type="button"
      variant="ghost"
      {...props}
    >
      {children}
    </Button>
  );
}
