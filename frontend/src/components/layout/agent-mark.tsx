import { cn } from "@/lib/utils";

export function AgentMark({ className }: { className?: string }) {
  return (
    <span aria-hidden="true" className={cn("agent-mark", className)}>
      <svg fill="none" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="harness-node-grad" x1="3" x2="21" y1="3" y2="21" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#38bdf8" />
            <stop offset="52%" stopColor="#0284c7" />
            <stop offset="100%" stopColor="#2563eb" />
          </linearGradient>
        </defs>
        <circle cx="8.2" cy="8.2" r="2.7" stroke="url(#harness-node-grad)" strokeWidth="2.1" />
        <circle cx="15.8" cy="15.8" r="2.7" stroke="url(#harness-node-grad)" strokeWidth="2.1" />
        <circle cx="15.8" cy="8.2" r="2.3" stroke="url(#harness-node-grad)" strokeWidth="1.9" />
        <circle cx="8.2" cy="15.8" r="2.3" stroke="url(#harness-node-grad)" strokeWidth="1.9" />
        <path
          d="M10.2 10.2 13.8 13.8M13.8 10.2 10.2 13.8"
          stroke="url(#harness-node-grad)"
          strokeLinecap="round"
          strokeWidth="2"
        />
      </svg>
    </span>
  );
}
