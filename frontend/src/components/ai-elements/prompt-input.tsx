"use client";

import type { ComponentProps } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type PromptInputProps = ComponentProps<"form">;

export function PromptInput({
  className,
  ...props
}: PromptInputProps) {
  return (
    <form
      className={cn(
        "rounded-2xl border bg-background p-2 shadow-sm transition-colors focus-within:ring-2 focus-within:ring-ring",
        className,
      )}
      {...props}
    />
  );
}

export type PromptInputBodyProps = ComponentProps<"div">;

export function PromptInputBody({
  className,
  ...props
}: PromptInputBodyProps) {
  return <div className={cn("min-w-0", className)} {...props} />;
}

export type PromptInputTextareaProps = ComponentProps<"textarea">;

export function PromptInputTextarea({
  className,
  ...props
}: PromptInputTextareaProps) {
  return (
    <textarea
      className={cn(
        "block max-h-40 min-h-12 w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-6 outline-none placeholder:text-muted-foreground",
        className,
      )}
      rows={2}
      {...props}
    />
  );
}

export type PromptInputFooterProps = ComponentProps<"div">;

export function PromptInputFooter({
  className,
  ...props
}: PromptInputFooterProps) {
  return (
    <div
      className={cn("mt-1 flex min-h-11 items-center justify-between gap-3", className)}
      {...props}
    />
  );
}

export type PromptInputToolsProps = ComponentProps<"div">;

export function PromptInputTools({
  className,
  ...props
}: PromptInputToolsProps) {
  return <div className={cn("flex items-center gap-1", className)} {...props} />;
}

export type PromptInputButtonProps = ComponentProps<typeof Button>;

export function PromptInputButton(props: PromptInputButtonProps) {
  return <Button type="button" variant="ghost" {...props} />;
}
