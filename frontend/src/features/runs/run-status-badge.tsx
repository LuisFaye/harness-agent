import { cn } from "@/lib/utils";

import { runStatusLabel, runStatusTone } from "./run-format";

export function RunStatusBadge({ status }: { status: string }) {
  const tone = runStatusTone(status);
  return (
    <span
      className={cn(
        "inline-flex min-h-7 items-center gap-1.5 rounded-full px-2.5 text-xs font-semibold",
        tone === "success" && "bg-success/10 text-success",
        tone === "danger" && "bg-destructive/10 text-destructive",
        tone === "warning" && "bg-warning/10 text-warning",
        tone === "info" && "bg-primary/10 text-primary",
      )}
    >
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      {runStatusLabel(status)}
    </span>
  );
}
