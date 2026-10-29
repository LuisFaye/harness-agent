import type { ConversationMessage, RunSummary, RunUsage, SessionSummary } from "@/lib/api/schemas";

const ACTIVE_RUN_STORAGE_PREFIX = "sb-agent-active-run:v2:";
const COMPLETED_SESSIONS_STORAGE_PREFIX = "sb-agent-completed-sessions:v2:";

export function bindOptimisticUserRunId(
  messages: ConversationMessage[],
  targetSessionId: string,
  runId: string,
): ConversationMessage[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.session_id !== targetSessionId || message.role !== "user") continue;
    if (message.run_id === runId) return messages;
    if (message.run_id == null) {
      const next = [...messages];
      next[index] = { ...message, run_id: runId };
      return next;
    }
    return messages;
  }
  return messages;
}

export function redactVisibleDatabaseSecret(value: string): string {
  return value
    .replace(
      /(\b(?:mysql_)?password\s*[:=]\s*)(['"]?)([^\s,;'"&]+)\2/gi,
      "$1[已移除，请使用安全表单]",
    )
    .replace(
      /((?:jdbc:)?mysql:\/\/[^:\s/@]+:)[^@\s]+(@)/gi,
      "$1[已移除]$2",
    );
}

export function normalizeRunUsage(value: unknown): RunUsage | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const numeric = (key: string): number | null => {
    const raw = record[key];
    return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
  };
  return {
    input_tokens: numeric("input_tokens"),
    output_tokens: numeric("output_tokens"),
    total_tokens: numeric("total_tokens"),
    cache_read_tokens: numeric("cache_read_tokens"),
    cache_miss_tokens: numeric("cache_miss_tokens"),
    cache_write_tokens: numeric("cache_write_tokens"),
    reasoning_tokens: numeric("reasoning_tokens"),
  };
}

const OPTIMISTIC_TITLE_MAX_CHARS = 36;
export const OPTIMISTIC_TITLE_FALLBACK = "新对话";
const MARKDOWN_LEADING_PATTERN = /^(?:#{1,6}\s+|[-*+]\s+|\d{1,3}[.、)]\s+)/;
const CONVERSATION_PREFIX_PATTERN = /^(?:你好[，,、\s]+|您好[，,、\s]+)?(?:请\s*帮我|请\s*帮忙|请\s*麻烦你|帮我一下|帮帮忙|麻烦你|请你|请问|帮我|帮忙)?/;
// 与后端标题生成规则一致，英文句号后须为空白或字符串末尾，避免截断域名和小数。
const SENTENCE_END_PATTERN = /[。！？!?]|\.(?=\s|$)/;
const SEMANTIC_TITLE_RULES: ReadonlyArray<readonly [RegExp, string]> = [
  [/^(?:你|您)?好(?:呀|啊|哇)?$|^(?:hi|hello|hey)$/i, "用户打招呼"],
  [/^(?:谢谢|多谢|感谢)(?:你|您)?(?:了|啦|啊)?$/i, "用户表示感谢"],
  [/^(?:再见|拜拜|回头见|bye|goodbye)$/i, "用户结束对话"],
  [/^(?:你是谁|你叫什么|介绍一下你自己)$/i, "了解助手身份"],
  [/^(?:你能做什么|你会做什么|你有哪些能力|你有什么功能)$/i, "了解助手能力"],
];

export function summarizeOptimisticSessionTitle(text: string): string {
  const cleaned = cleanOptimisticTitleText(text);
  if (!cleaned) return OPTIMISTIC_TITLE_FALLBACK;
  for (const [pattern, title] of SEMANTIC_TITLE_RULES) {
    if (pattern.test(cleaned.trim())) return title;
  }
  return cleaned.slice(0, OPTIMISTIC_TITLE_MAX_CHARS);
}

function cleanOptimisticTitleText(text: string): string {
  const lines: string[] = [];
  let inFence = false;
  for (const rawLine of text.split(/\r?\n/)) {
    const stripped = rawLine.trim();
    if (stripped.startsWith("```") || stripped.startsWith("~~~")) {
      inFence = !inFence;
      continue;
    }
    if (!inFence) lines.push(stripped.replace(MARKDOWN_LEADING_PATTERN, ""));
  }
  let cleaned = lines.join("\n");
  for (let round = 0; round < 3; round += 1) {
    const next = cleaned.replace(CONVERSATION_PREFIX_PATTERN, "");
    if (next === cleaned) break;
    cleaned = next;
  }
  return firstTitleSentence(cleaned);
}

function firstTitleSentence(text: string): string {
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const endIndex = line.search(SENTENCE_END_PATTERN);
    if (endIndex >= 0) {
      const firstSentence = line.slice(0, endIndex).trim();
      if (firstSentence) return firstSentence.replace(/\s+/g, " ").trim();
    } else {
      return line.replace(/\s+/g, " ").trim();
    }
  }
  return "";
}

const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "cancel_requested"]);

export function readCompletedSessionIds(userId: string): string[] {
  try {
    const raw = window.localStorage.getItem(`${COMPLETED_SESSIONS_STORAGE_PREFIX}${userId}`);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    // 存储损坏或不可用时，未读标记退化为当前页面内维护。
    return [];
  }
}

export function writeCompletedSessionIds(userId: string, sessionIds: string[]) {
  try {
    window.localStorage.setItem(`${COMPLETED_SESSIONS_STORAGE_PREFIX}${userId}`, JSON.stringify(sessionIds));
  } catch {
    // 持久化失败不应打断对话。
  }
}

export function activeRunStorageKey(userId: string, sessionId: string): string {
  return `${ACTIVE_RUN_STORAGE_PREFIX}${userId}:${sessionId}`;
}

export function isActiveRunStatus(status: string): boolean {
  return ACTIVE_RUN_STATUSES.has(status);
}

export function synthesizeActiveSession(run: RunSummary): SessionSummary {
  return {
    session_id: run.session_id,
    title: summarizeOptimisticSessionTitle(run.query_summary),
    preview: run.query_summary,
    message_count: 1,
    last_message_at: run.started_at,
    has_summary: false,
  };
}

export function matchesSessionKeyword(session: SessionSummary, keyword: string): boolean {
  if (!keyword) return true;
  const needle = keyword.toLowerCase();
  return session.session_id.toLowerCase().includes(needle)
    || session.title.toLowerCase().includes(needle)
    || session.preview.toLowerCase().includes(needle);
}

export function isSessionWithinDateRange(value: string | null, range: { dateFrom?: string; dateTo?: string }): boolean {
  if (!range.dateFrom && !range.dateTo) return true;
  if (!value) return false;
  if (range.dateFrom && value < range.dateFrom) return false;
  if (range.dateTo && value >= range.dateTo) return false;
  return true;
}
