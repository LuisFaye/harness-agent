import { Orbit } from "lucide-react";

import { cn } from "@/lib/utils";

export function AgentMark({ className }: { className?: string }) {
  return (
    <span aria-hidden="true" className={cn("agent-mark", className)}>
      <Orbit strokeWidth={1.6} />
    </span>
  );
}
