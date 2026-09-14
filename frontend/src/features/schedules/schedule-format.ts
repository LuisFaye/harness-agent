import type { ScheduledJob } from "@/lib/api/management-schemas";

export function formatScheduleDate(value: string | null): string {
  if (!value) {
    return "尚未执行";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function scheduleStatus(status: string) {
  if (status === "enabled") {
    return { label: "已启用", tone: "success" as const };
  }
  if (status === "paused") {
    return { label: "已暂停", tone: "warning" as const };
  }
  if (status === "error") {
    return { label: "异常", tone: "danger" as const };
  }
  return { label: status || "未知", tone: "neutral" as const };
}

export function scheduleRunStatus(status: string) {
  if (status === "success") {
    return { label: "成功", tone: "success" as const };
  }
  if (status === "running") {
    return { label: "运行中", tone: "warning" as const };
  }
  if (status === "error") {
    return { label: "失败", tone: "danger" as const };
  }
  return { label: status || "未知", tone: "neutral" as const };
}

export function scheduleKindLabel(job: ScheduledJob): string {
  if (job.schedule_kind === "daily") {
    return `每天 ${job.schedule_time}`;
  }
  return `${job.schedule_kind} · ${job.schedule_time}`;
}

export function scheduleConfigSummary(configJson: string | null): string[] {
  if (!configJson) {
    return [];
  }
  try {
    const value = JSON.parse(configJson) as Record<string, unknown>;
    const repos = Array.isArray(value.repos)
      ? value.repos.filter((item): item is string => typeof item === "string")
      : [];
    const author = typeof value.author === "string" && value.author.trim()
      ? `作者：${value.author.trim()}`
      : null;
    return [
      ...repos.slice(0, 3).map((repo) => `仓库：${repo}`),
      repos.length > 3 ? `另有 ${repos.length - 3} 个仓库` : null,
      author,
    ].filter((item): item is string => Boolean(item));
  } catch {
    return ["任务配置暂时无法解析"];
  }
}

export function compactScheduleText(value: string | null, maxLength = 180): string {
  const text = (value || "").replace(/\s+/g, " ").trim();
  if (!text) {
    return "没有输出摘要";
  }
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}
