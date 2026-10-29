import type { ToolState } from "@/components/ai-elements/tool";
import type { TraceItem } from "@/lib/stream/schemas";

export type CapabilityKind = "MCP" | "Skill" | "Sub-Agent" | "Tool";

export interface ReasoningViewModel {
  content: string;
  isStreaming: boolean;
  isTruncated: boolean;
  key: string;
  segment: number;
  summary: string;
}

export interface NarrativeViewModel {
  isStreaming: boolean;
  key: string;
  text: string;
}

export interface ToolCallViewModel {
  defaultOpen: boolean;
  durationMs: number | null;
  error: unknown;
  input: unknown;
  key: string;
  kind: CapabilityKind;
  name: string;
  output: unknown;
  sourceLabel: string | null;
  state: ToolState;
  summary: string;
  title: string;
}

export type ExecutionViewModel =
  | { kind: "reasoning"; reasoning: ReasoningViewModel }
  | { kind: "narrative"; narrative: NarrativeViewModel }
  | { kind: "tool"; tool: ToolCallViewModel };

export function buildExecutionViewModels(
  items: TraceItem[],
  runActive = true,
): ExecutionViewModel[] {
  const result: ExecutionViewModel[] = [];
  const indexByIdentity = new Map<string, number>();

  for (const item of items) {
    if (item.stage === "iteration_started") {
      if (item.details?.public_progress === false) continue;
      const narrative = toNarrativeViewModel(item, runActive);
      const previous = [...result].reverse().find((entry) => entry.kind === "narrative");
      if (previous?.kind === "narrative" && previous.narrative.text === narrative.text) continue;
      result.push({
        kind: "narrative",
        narrative,
      });
      continue;
    }
    if (item.stage.startsWith("iteration_")) {
      continue;
    }
    if (!isDisplayableTrace(item)) {
      continue;
    }

    const identity = traceIdentity(item);
    const viewModel: ExecutionViewModel = item.stage === "reasoning"
      ? { kind: "reasoning", reasoning: toReasoningViewModel(item, runActive) }
      : { kind: "tool", tool: toToolCallViewModel(item, runActive) };
    const existingIndex = indexByIdentity.get(identity);
    if (existingIndex !== undefined) {
      result[existingIndex] = viewModel;
      continue;
    }
    indexByIdentity.set(identity, result.length);
    result.push(viewModel);
  }

  return result;
}

export function toReasoningViewModel(
  item: TraceItem,
  runActive = true,
): ReasoningViewModel {
  const isStreaming = runActive && item.status === "running";
  const segment = reasoningSegment(item);
  return {
    content: textValue(item.details?.reasoning_content),
    isStreaming,
    isTruncated: Boolean(item.details?.truncated),
    key: item.key || reasoningIdentity(segment),
    segment,
    summary: isStreaming
      ? segment > 1 ? `第 ${segment} 次思考` : "正在思考"
      : segment > 1 ? `第 ${segment} 次思考 · 已完成` : "已完成思考",
  };
}

export function toNarrativeViewModel(
  item: TraceItem,
  runActive = true,
): NarrativeViewModel {
  const iteration = item.details?.iteration;
  return {
    isStreaming: runActive && item.status === "running",
    key: item.key || `iteration-${String(iteration ?? "step")}`,
    text: narrativeFromIteration(item),
  };
}

export function toToolCallViewModel(item: TraceItem, runActive = true): ToolCallViewModel {
  const details = item.details ?? {};
  const staleRunning = !runActive && item.status === "running";
  const state = staleRunning ? "pending" : mapToolState(item.status);
  const error = publicError(details.error_message);
  const output = publicOutput(details.output_summary ?? details.result);
  const name = textValue(details.tool);
  return {
    defaultOpen: state === "blocked" || state === "error",
    durationMs: numberValue(details.duration_ms),
    error,
    input: sanitizeValue(details.inputs),
    key: item.key,
    kind: capabilityKind(item),
    name,
    output,
    sourceLabel: capabilitySource(details),
    state,
    summary: staleRunning ? "执行未返回最终结果" : toolSummary(item, state, error),
    title: capabilityTitle(item, name),
  };
}

export function toolStatusLabel(item: ToolCallViewModel): string {
  if (item.state === "running") {
    return "调用中";
  }
  if (item.state === "error") {
    return "失败";
  }
  if (item.state === "blocked") {
    return "等待";
  }
  return "完成";
}

export function toolNarrative(item: ToolCallViewModel): string {
  if (item.key === "final") {
    return item.state === "running" ? "正在整理回复" : "回复已整理好";
  }
  if (item.key === "citations") {
    return item.state === "running" ? "正在核对引用来源" : "引用来源已核对";
  }
  if (item.state === "running") {
    return item.title;
  }
  if (item.state === "error") {
    return `${item.title} 执行失败`;
  }
  if (item.state === "blocked") {
    return item.summary;
  }
  return item.title;
}

export function isStepActive(
  item: ExecutionViewModel,
): boolean {
  if (item.kind === "reasoning") {
    return item.reasoning.isStreaming;
  }
  if (item.kind === "narrative") {
    return item.narrative.isStreaming;
  }
  return item.tool.state === "running";
}

export function mapToolState(status: string): ToolState {
  if (status === "running") {
    return "running";
  }
  if (status === "success" || status === "completed") {
    return "completed";
  }
  if (status === "blocked" || status === "needs_input" || status === "needs_confirmation") {
    return "blocked";
  }
  if (status === "failed" || status === "error") {
    return "error";
  }
  return "pending";
}

function narrativeFromIteration(item: TraceItem): string {
  const details = item.details ?? {};
  const tool = textValue(details.name) || textValue(details.tool);
  if (item.summary && item.summary !== item.title) {
    return item.summary;
  }
  if (tool) {
    return `接下来调用 ${capabilityTitle(item, tool)}。`;
  }
  return item.title || "正在规划下一步。";
}

function capabilityKind(item: TraceItem): CapabilityKind {
  const details = item.details ?? {};
  const source = [
    item.stage,
    details.capability_type,
    details.kind,
    details.provider,
    details.skill_name,
    details.tool,
  ].map(String).join(" ").toLowerCase();
  if (source.includes("sub_agent") || source.includes("sub-agent")) {
    return "Sub-Agent";
  }
  if (source.includes("mcp")) {
    return "MCP";
  }
  if (!textValue(details.tool) && source.includes("skill")) {
    return "Skill";
  }
  return "Tool";
}

function toolSummary(item: TraceItem, state: ToolState, error: unknown): string {
  if (state === "running") {
    return item.summary || "正在执行";
  }
  if (state === "error") {
    return textValue(error) || "执行失败，可展开查看错误信息";
  }
  if (state === "blocked") {
    return item.status === "needs_input" ? "等待补充信息" : "调用已拦截";
  }
  if (state === "completed") {
    return "已成功返回结果";
  }
  if (item.summary && item.summary !== item.title) {
    return item.summary;
  }
  return {
    blocked: item.status === "needs_input" ? "等待补充信息" : "等待确认",
    completed: "已完成",
    error: "执行失败",
    pending: "等待执行",
    running: "正在执行",
  }[state];
}

function isCapabilityTrace(item: TraceItem): boolean {
  const stage = item.stage.toLowerCase();
  return Boolean(textValue(item.details?.tool))
    || ["tool", "skill", "mcp", "sub_agent", "sub-agent"].some((part) => stage.includes(part));
}

function isDisplayableTrace(item: TraceItem): boolean {
  if (item.stage === "reasoning" || isCapabilityTrace(item)) {
    return true;
  }
  return [
    "citation_verification",
    "final_response",
    "tool_blocked",
    "waiting_input",
  ].includes(item.stage);
}

function reasoningSegment(item: TraceItem): number {
  const fromDetails = item.details?.segment;
  if (typeof fromDetails === "number" && Number.isFinite(fromDetails) && fromDetails > 0) {
    return fromDetails;
  }
  const match = item.key.match(/^reasoning(?:-(\d+))?$/);
  if (match?.[1]) {
    return Number(match[1]);
  }
  return 1;
}

function reasoningIdentity(segment: number): string {
  return `reasoning:${segment}`;
}

function traceIdentity(item: TraceItem): string {
  if (item.stage === "reasoning") {
    return reasoningIdentity(reasoningSegment(item));
  }
  if (item.stage === "final_response") {
    return "final";
  }
  if (item.stage === "citation_verification") {
    return "citations";
  }
  const tool = textValue(item.details?.tool);
  const iteration = typeof item.details?.iteration === "number" ? item.details.iteration : null;
  if (tool && iteration !== null && ["tool_started", "tool_finished", "tool_blocked"].includes(item.stage)) {
    return `tool:${tool}:${iteration}`;
  }
  const capability = tool || textValue(item.details?.skill_name);
  return capability ? `capability:${capability}` : item.key;
}

function capabilityTitle(item: TraceItem, name: string): string {
  const knownTitles: Record<string, string> = {
    agent_browser: "浏览器操作",
    daily_report: "生成工作日报",
    mcp_web_search_bailian_web_search: "联网搜索",
    read_run_evidence: "读取完整证据",
    weather_query: "查询天气",
    web_fetch: "读取网页正文",
  };
  return knownTitles[name] || item.title || "执行能力";
}

function capabilitySource(details: Record<string, unknown>): string | null {
  const skillName = textValue(details.skill_name);
  return skillName ? `来自 Skill · ${skillName}` : null;
}

function publicError(value: unknown): string | null {
  const error = textValue(value);
  if (!error) {
    return null;
  }
  return error.split(/\r?\n/, 1)[0]?.slice(0, 300) || "执行失败";
}

function publicOutput(value: unknown): unknown {
  const text = textValue(value);
  if (!text) {
    return value === null || value === undefined ? null : sanitizeValue(value);
  }
  if (text.startsWith("[长工具结果已保存]") || text.includes("original_estimated_tokens:")) {
    return "完整结果已安全保存，可在运行详情中查看。";
  }
  if (/^\s*[\[{]/.test(text)) {
    return "已返回结构化结果，可在运行详情中查看。";
  }
  return text.slice(0, 300);
}

function sanitizeValue(value: unknown, key = ""): unknown {
  if (value === null || value === undefined) {
    return value;
  }
  if (["authorization", "api_key", "apikey", "cookie", "password", "secret", "token"].some(
    (part) => key.toLowerCase().includes(part),
  )) {
    return "••••••";
  }
  if (typeof value === "string") {
    if (/^https?:\/\//i.test(value)) {
      try {
        const url = new URL(value);
        return `${url.origin}${url.pathname}`;
      } catch {
        return value.slice(0, 300);
      }
    }
    return value.slice(0, 300);
  }
  if (Array.isArray(value)) {
    return value.slice(0, 20).map((entry) => sanitizeValue(entry));
  }
  if (typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .slice(0, 30)
        .map(([entryKey, entry]) => [entryKey, sanitizeValue(entry, entryKey)]),
    );
  }
  return value;
}

function textValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
