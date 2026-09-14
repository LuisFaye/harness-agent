import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Database,
  FileCode2,
  FolderGit2,
  Globe2,
  Power,
  Search,
  SquareTerminal,
  Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import type { ToolDefinition } from "@/lib/api/management-schemas";

import { compactCapabilityText, riskLabel } from "./capability-format";

const PAGE_SIZE = 6;

interface ToolsPanelProps {
  items: ToolDefinition[];
  pendingToolId: string | null;
  onToggle: (tool: ToolDefinition) => void;
}

export function ToolsPanel({ items, pendingToolId, onToggle }: ToolsPanelProps) {
  const [keyword, setKeyword] = useState("");
  const [availability, setAvailability] = useState("all");
  const [sourceType, setSourceType] = useState("all");
  const [page, setPage] = useState(1);
  const sourceTypes = useMemo(
    () => Array.from(new Set(items.map((item) => item.source_type))).sort(),
    [items],
  );
  const filtered = useMemo(() => {
    const normalized = keyword.trim().toLowerCase();
    return items.filter((item) => {
      if (normalized && !`${item.name} ${item.description}`.toLowerCase().includes(normalized)) {
        return false;
      }
      if (availability === "available" && !item.available) {
        return false;
      }
      if (availability === "unavailable" && (!item.enabled || item.available)) {
        return false;
      }
      if (availability === "disabled" && item.enabled) {
        return false;
      }
      if (sourceType !== "all" && item.source_type !== sourceType) {
        return false;
      }
      return true;
    });
  }, [availability, items, keyword, sourceType]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visibleItems = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  function changeFilter(update: () => void) {
    update();
    setPage(1);
  }

  if (!items.length) {
    return <EmptyPanel text="当前没有可注册的 Tool。" />;
  }

  return (
    <div>
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_11rem_11rem]">
        <label className="relative block">
          <span className="sr-only">搜索 Tool 名称或描述</span>
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <input
            className="min-h-11 w-full rounded-lg border border-border bg-card pr-3 pl-9 text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25"
            onChange={(event) => changeFilter(() => setKeyword(event.target.value))}
            placeholder="搜索名称或描述"
            type="search"
            value={keyword}
          />
        </label>
        <label className="relative block">
          <span className="sr-only">按可用状态筛选</span>
          <select
            aria-label="按可用状态筛选"
            className="min-h-11 w-full rounded-lg border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25"
            onChange={(event) => changeFilter(() => setAvailability(event.target.value))}
            value={availability}
          >
            <option value="all">全部状态</option>
            <option value="available">可调用</option>
            <option value="unavailable">不可用</option>
            <option value="disabled">已停用</option>
          </select>
        </label>
        <label className="relative block">
          <span className="sr-only">按来源类型筛选</span>
          <select
            aria-label="按来源类型筛选"
            className="min-h-11 w-full rounded-lg border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25"
            onChange={(event) => changeFilter(() => setSourceType(event.target.value))}
            value={sourceType}
          >
            <option value="all">全部来源</option>
            {sourceTypes.map((source) => <option key={source} value={source}>{source}</option>)}
          </select>
        </label>
      </div>

      <p aria-live="polite" className="mt-3 text-xs text-muted-foreground">
        共 {filtered.length} 个 Tool
        {filtered.length !== items.length ? `（已筛选，共 ${items.length} 个）` : ""}
      </p>

      {visibleItems.length ? (
        <div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {visibleItems.map((item) => (
            <article
              className="flex h-full min-w-0 flex-col rounded-xl border border-border/80 bg-card p-4 transition-[border-color,box-shadow] duration-200 hover:border-primary/35 hover:shadow-sm hover:shadow-primary/5"
              key={item.tool_id}
            >
              <header className="flex items-start gap-3">
                <span
                  aria-hidden="true"
                  className={[
                    "grid size-10 shrink-0 place-items-center rounded-lg border",
                    sourceIconTone(item.source_type),
                  ].join(" ")}
                >
                  <SourceIcon sourceType={item.source_type} />
                </span>
                <div className="min-w-0 flex-1">
                  <h2 className="truncate font-semibold" title={item.name}>{item.name}</h2>
                  <p className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">
                    {compactCapabilityText(item.description || "暂无说明")}
                  </p>
                </div>
                <AvailabilityBadge available={item.available} enabled={item.enabled} />
              </header>
              <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
                <span className="rounded-full border border-border bg-muted/50 px-2 py-0.5 text-muted-foreground">
                  {item.source_type} · {item.source_id}
                </span>
                <span className="rounded-full border border-border bg-muted/50 px-2 py-0.5 text-muted-foreground">
                  {riskLabel(item.risk_level)}
                </span>
                {item.requires_confirmation ? (
                  <span className="rounded-full border border-warning/25 bg-warning/10 px-2 py-0.5 text-warning">
                    执行前确认
                  </span>
                ) : null}
              </div>
              <div className="mt-auto flex items-center justify-between gap-3 border-t border-border/60 pt-3">
                <span
                  className={`flex min-w-0 items-center gap-1.5 text-xs ${item.available ? "text-success" : "text-warning"}`}
                >
                  {item.available
                    ? <CheckCircle2 aria-hidden="true" className="size-3.5 shrink-0" />
                    : <AlertTriangle aria-hidden="true" className="size-3.5 shrink-0" />}
                  <span className="truncate">
                    {item.unavailable_reason || item.last_error || "运行条件正常，可由 Agent 调用。"}
                  </span>
                </span>
                <Button
                  aria-label={item.enabled ? `停用 ${item.name}` : `启用 ${item.name}`}
                  className="shrink-0"
                  disabled={pendingToolId === item.tool_id}
                  onClick={() => onToggle(item)}
                  size="sm"
                  variant="ghost"
                >
                  <Power aria-hidden="true" />
                  {item.enabled ? "停用 Tool" : "启用 Tool"}
                </Button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <EmptyPanel text="没有匹配的 Tool。" />
      )}

      {filtered.length > PAGE_SIZE ? (
        <nav
          aria-label="Tool 列表分页"
          className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"
        >
          <span className="text-xs text-muted-foreground">
            第 {safePage} / {totalPages} 页
          </span>
          <div className="flex flex-wrap items-center gap-1">
            <Button
              aria-label="上一页"
              disabled={safePage <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              size="sm"
              variant="outline"
            >
              <ChevronLeft aria-hidden="true" />
              上一页
            </Button>
            {Array.from({ length: totalPages }, (_, index) => index + 1).map((number) => (
              <Button
                aria-current={number === safePage ? "page" : undefined}
                aria-label={`第 ${number} 页`}
                className="min-w-9 px-0"
                key={number}
                onClick={() => setPage(number)}
                size="sm"
                variant={number === safePage ? "default" : "ghost"}
              >
                {number}
              </Button>
            ))}
            <Button
              aria-label="下一页"
              disabled={safePage >= totalPages}
              onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              size="sm"
              variant="outline"
            >
              下一页
              <ChevronRight aria-hidden="true" />
            </Button>
          </div>
        </nav>
      ) : null}
    </div>
  );
}

function SourceIcon({ sourceType }: { sourceType: ToolDefinition["source_type"] }) {
  if (sourceType === "mcp") {
    return <Globe2 aria-hidden="true" className="size-5" />;
  }
  if (sourceType === "database") {
    return <Database aria-hidden="true" className="size-5" />;
  }
  if (sourceType === "workspace") {
    return <FolderGit2 aria-hidden="true" className="size-5" />;
  }
  if (sourceType === "shell") {
    return <SquareTerminal aria-hidden="true" className="size-5" />;
  }
  if (sourceType === "git") {
    return <FileCode2 aria-hidden="true" className="size-5" />;
  }
  if (sourceType === "skill") {
    return <Bot aria-hidden="true" className="size-5" />;
  }
  return <Wrench aria-hidden="true" className="size-5" />;
}

function sourceIconTone(sourceType: ToolDefinition["source_type"]): string {
  const tones: Record<ToolDefinition["source_type"], string> = {
    skill: "border-primary/20 bg-primary-soft text-primary",
    mcp: "border-sky-500/20 bg-sky-500/10 text-sky-600 dark:text-sky-400",
    database: "border-emerald-500/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    workspace: "border-amber-500/20 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    shell: "border-slate-500/20 bg-slate-500/10 text-slate-600 dark:text-slate-300",
    git: "border-rose-500/20 bg-rose-500/10 text-rose-600 dark:text-rose-400",
    artifact: "border-violet-500/20 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  };
  return tones[sourceType];
}

function AvailabilityBadge({ enabled, available }: { enabled: boolean; available: boolean }) {
  const label = available ? "可调用" : enabled ? "不可用" : "已停用";
  const className = available
    ? "border-success/25 bg-success/10 text-success"
    : enabled
      ? "border-warning/25 bg-warning/10 text-warning"
      : "border-border bg-muted text-muted-foreground";
  return <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${className}`}>{label}</span>;
}

function EmptyPanel({ text }: { text: string }) {
  return <div className="grid min-h-48 place-items-center rounded-xl border border-dashed bg-card text-sm text-muted-foreground">{text}</div>;
}
