import { cn } from "@/lib/utils";

import { runStatusLabel, runStatusTone } from "./run-format";

export function RunStatusBadge({ status }: { status: string }) {
  const tone = runStatusTone(status);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        tone === "success" && "border-success/20 bg-success/10 text-success",
        tone === "danger" && "border-destructive/20 bg-destructive/10 text-destructive",
        tone === "warning" && "border-warning/20 bg-warning/10 text-warning",
        tone === "info" && "border-primary/20 bg-primary/10 text-primary",
      )}
    >
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      {runStatusLabel(status)}
    </span>
  );
}
