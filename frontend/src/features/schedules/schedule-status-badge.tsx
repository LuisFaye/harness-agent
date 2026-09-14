interface ScheduleStatusBadgeProps {
  label: string;
  tone: "success" | "warning" | "danger" | "neutral";
}

const toneClass = {
  success: "border-success/25 bg-success/10 text-success",
  warning: "border-warning/25 bg-warning/10 text-warning",
  danger: "border-destructive/25 bg-destructive/10 text-destructive",
  neutral: "border-border bg-muted text-muted-foreground",
};

export function ScheduleStatusBadge({ label, tone }: ScheduleStatusBadgeProps) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${toneClass[tone]}`}>
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}
