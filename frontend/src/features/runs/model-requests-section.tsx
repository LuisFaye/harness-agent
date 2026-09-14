import { Check, ChevronDown, Copy, MessageSquareCode, Wrench } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { isRecord, textValue } from "./run-format";

type ModelRequestRecord = {
  turn?: number;
  captured_at?: string;
  model?: string | null;
  stream?: boolean;
  message_count?: number;
  tool_count?: number;
  roles?: string[];
  truncated?: boolean;
  request?: Record<string, unknown> | null;
  dropped_earlier_turns?: boolean;
};

const ROLE_STYLES: Record<string, string> = {
  system: "border-sky-500/30 bg-sky-500/10 text-sky-800 dark:text-sky-200",
  user: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200",
  assistant: "border-amber-500/30 bg-amber-500/10 text-amber-900 dark:text-amber-100",
  tool: "border-slate-500/30 bg-slate-500/10 text-slate-800 dark:text-slate-200",
};

const ROLE_LABELS: Record<string, string> = {
  system: "system",
  user: "user",
  assistant: "assistant",
  tool: "tool",
};

export function ModelRequestsSection({ items }: { items: unknown[] | null | undefined }) {
  const records = useMemo(() => normalizeRecords(items), [items]);
  const [activeTurn, setActiveTurn] = useState(0);
  const [showRaw, setShowRaw] = useState(false);
  const [copied, setCopied] = useState(false);

  const active = records[Math.min(activeTurn, Math.max(records.length - 1, 0))] ?? null;
  const request = active && isRecord(active.request) ? active.request : null;
  const messages = Array.isArray(request?.messages)
    ? request.messages.filter(isRecord)
    : [];
  const tools = Array.isArray(request?.tools) ? request.tools.filter(isRecord) : [];
  const omitted = Boolean(request?.omitted);
  const rawJson = useMemo(() => JSON.stringify(request ?? {}, null, 2), [request]);

  if (!records.length || !active) {
    return null;
  }

  async function copyRaw() {
    try {
      await navigator.clipboard.writeText(rawJson);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section aria-labelledby="model-requests-title" className="run-detail-section">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2" id="model-requests-title">
            <MessageSquareCode aria-hidden="true" className="size-4 text-primary" />
            模型请求
            <span className="rounded-md bg-secondary px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
              OpenAI 兼容
            </span>
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            按轮次展示发给模型的完整 chat.completions 请求体；仅所有者可见，本地短命存档（约 7 天 / 最多 50 次）。
          </p>
        </div>
        {active.truncated ? (
          <span className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-800 dark:text-amber-200">
            本轮已截断
          </span>
        ) : null}
      </div>

      <div className="mt-4 flex flex-wrap gap-2" role="tablist" aria-label="模型请求轮次">
        {records.map((item, index) => {
          const turn = Number(item.turn ?? index + 1);
          const selected = index === Math.min(activeTurn, records.length - 1);
          return (
            <button
              aria-selected={selected}
              className={cn(
                "min-h-11 rounded-lg border px-3 text-sm transition-colors",
                selected
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-muted/40 text-foreground hover:bg-muted",
              )}
              key={`turn-${turn}-${index}`}
              onClick={() => {
                setActiveTurn(index);
                setShowRaw(false);
              }}
              role="tab"
              type="button"
            >
              第 {turn} 轮
              {item.truncated ? <span className="ml-1 opacity-80">·截断</span> : null}
            </button>
          );
        })}
      </div>

      <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetaItem label="模型" value={textValue(active.model || request?.model) || "未记录"} />
        <MetaItem label="消息数" value={String(active.message_count ?? messages.length)} />
        <MetaItem label="Tools" value={String(active.tool_count ?? tools.length)} />
        <MetaItem
          label="流式"
          value={active.stream || request?.stream ? "stream=true" : "否"}
        />
      </dl>

      {active.dropped_earlier_turns ? (
        <p className="mt-3 text-xs text-muted-foreground">
          本轮之前还有更早的请求因轮次上限未保留。
        </p>
      ) : null}

      {omitted ? (
        <p className="mt-4 rounded-lg border border-dashed bg-muted/40 p-4 text-sm text-muted-foreground">
          本轮完整请求体因体积超限未保留，仅留下摘要字段。
        </p>
      ) : (
        <>
          <div className="mt-5">
            <h3 className="text-sm font-semibold">messages</h3>
            <div className="mt-2 grid gap-2">
              {messages.length ? messages.map((message, index) => (
                <MessageCard key={`msg-${index}`} message={message} index={index} />
              )) : (
                <p className="text-sm text-muted-foreground">本轮没有 messages。</p>
              )}
            </div>
          </div>

          {tools.length ? (
            <details className="mt-5 group">
              <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 text-sm font-semibold">
                <Wrench aria-hidden="true" className="size-4 text-primary" />
                tools（{tools.length}）
                <ChevronDown
                  aria-hidden="true"
                  className="size-4 text-muted-foreground transition-transform group-open:rotate-180"
                />
              </summary>
              <div className="mt-2 grid gap-2">
                {tools.map((tool, index) => {
                  const fn = isRecord(tool.function) ? tool.function : {};
                  return (
                    <article className="rounded-lg border bg-muted/20 p-3" key={`tool-${index}`}>
                      <div className="flex flex-wrap items-center gap-2">
                        <code className="text-sm font-semibold">
                          {textValue(fn.name) || `tool_${index + 1}`}
                        </code>
                        <span className="text-xs text-muted-foreground">
                          {textValue(tool.type) || "function"}
                        </span>
                      </div>
                      {textValue(fn.description) ? (
                        <p className="mt-1 text-sm text-muted-foreground">{textValue(fn.description)}</p>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            </details>
          ) : null}

          <div className="mt-5 flex flex-wrap items-center gap-2">
            <Button
              onClick={() => setShowRaw((value) => !value)}
              size="sm"
              type="button"
              variant="outline"
            >
              {showRaw ? "收起原始 JSON" : "查看原始 JSON"}
            </Button>
            <Button onClick={() => void copyRaw()} size="sm" type="button" variant="ghost">
              {copied ? <Check aria-hidden="true" className="size-4" /> : <Copy aria-hidden="true" className="size-4" />}
              {copied ? "已复制" : "复制 JSON"}
            </Button>
          </div>
          {showRaw ? (
            <pre className="mt-3 max-h-[28rem] overflow-auto rounded-lg border bg-muted/50 p-3 font-mono text-xs leading-relaxed">
              {rawJson}
            </pre>
          ) : null}
        </>
      )}
    </section>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-muted/30 p-3">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-1 truncate text-sm font-medium" title={value}>{value}</dd>
    </div>
  );
}

function MessageCard({ message, index }: { message: Record<string, unknown>; index: number }) {
  const role = String(message.role || "unknown").toLowerCase();
  const style = ROLE_STYLES[role] || "border-border bg-muted/30 text-foreground";
  const label = ROLE_LABELS[role] || role;
  const content = formatMessageContent(message.content);
  const toolCalls = Array.isArray(message.tool_calls)
    ? message.tool_calls.filter(isRecord)
    : [];
  const toolCallId = textValue(message.tool_call_id);

  return (
    <article className={cn("rounded-lg border p-3", style)}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-md bg-background/70 px-2 py-0.5 font-mono text-xs font-semibold">
          #{index + 1} {label}
        </span>
        {toolCallId ? (
          <code className="truncate text-xs opacity-80">tool_call_id={toolCallId}</code>
        ) : null}
        {message.content_truncated ? (
          <span className="text-xs opacity-80">内容已截断</span>
        ) : null}
      </div>
      {content ? (
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs leading-relaxed opacity-95">
          {content}
        </pre>
      ) : null}
      {toolCalls.length ? (
        <div className="mt-3 grid gap-2">
          {toolCalls.map((call, callIndex) => {
            const fn = isRecord(call.function) ? call.function : {};
            return (
              <div className="rounded-md border border-black/5 bg-background/50 p-2 dark:border-white/10" key={`call-${callIndex}`}>
                <code className="text-xs font-semibold">
                  {textValue(fn.name) || "function"}
                </code>
                {textValue(fn.arguments) ? (
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed opacity-90">
                    {textValue(fn.arguments)}
                  </pre>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </article>
  );
}

function normalizeRecords(items: unknown[] | null | undefined): ModelRequestRecord[] {
  if (!Array.isArray(items) || !items.length) {
    return [];
  }
  return items
    .filter(isRecord)
    .map((item) => item as ModelRequestRecord);
}

function formatMessageContent(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (!isRecord(part)) {
          return String(part);
        }
        if (part.type === "text") {
          return textValue(part.text);
        }
        if (part.omitted) {
          return `[${textValue(part.type) || "part"} omitted]`;
        }
        return JSON.stringify(part);
      })
      .filter(Boolean)
      .join("\n");
  }
  if (content == null) {
    return "";
  }
  try {
    return JSON.stringify(content, null, 2);
  } catch {
    return String(content);
  }
}
