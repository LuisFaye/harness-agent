import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Search,
  Sparkles,
  Upload,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import type { SkillSummary } from "@/lib/api/management-schemas";

import { compactCapabilityText, compatibilityLabel } from "./capability-format";

const PAGE_SIZE = 6;

type StatusFilter = "all" | "callable" | "enabled" | "disabled";

interface SkillsPanelProps {
  items: SkillSummary[];
  importing: boolean;
  onImport: (path: string) => void;
  onSelect: (skill: SkillSummary) => void;
}

export function SkillsPanel({ items, importing, onImport, onSelect }: SkillsPanelProps) {
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [source, setSource] = useState("all");
  const [page, setPage] = useState(1);
  const [path, setPath] = useState("");
  const sources = useMemo(
    () => Array.from(new Set(items.map((item) => item.source))).sort(
      (left, right) => left.localeCompare(right),
    ),
    [items],
  );
  const filtered = useMemo(() => {
    const normalized = keyword.trim().toLowerCase();
    return items.filter((item) => {
      if (normalized && ![
        item.name,
        item.description,
        ...item.capabilities,
      ].join(" ").toLowerCase().includes(normalized)) {
        return false;
      }
      if (status === "callable" && !item.callable_by_agent) {
        return false;
      }
      if (status === "enabled" && (!item.enabled || item.callable_by_agent)) {
        return false;
      }
      if (status === "disabled" && item.enabled) {
        return false;
      }
      if (source !== "all" && item.source !== source) {
        return false;
      }
      return true;
    });
  }, [items, keyword, source, status]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visibleItems = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  function changeFilter(update: () => void) {
    update();
    setPage(1);
  }

  function submitImport(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = path.trim();
    if (!value) {
      return;
    }
    onImport(value);
    setPath("");
  }

  return (
    <div className="space-y-4">
      <details className="group rounded-2xl border border-border/85 bg-card shadow-2xs">
        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-2xl px-4 py-2.5 text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <span className="flex items-center gap-2.5">
            <span className="grid size-7 place-items-center rounded-lg bg-primary-soft text-primary">
              <Upload aria-hidden="true" className="size-3.5" />
            </span>
            导入 Skill
          </span>
          <ChevronDown
            aria-hidden="true"
            className="size-4 text-muted-foreground transition-transform duration-200 group-open:rotate-180"
          />
        </summary>
        <form className="border-t border-border/75 px-4 py-4" onSubmit={submitImport}>
          <label className="text-sm font-medium" htmlFor="skill-import-path">
            Skill 路径
          </label>
          <input
            className="mt-2 h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground shadow-2xs outline-none transition-all placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25"
            id="skill-import-path"
            onChange={(event) => setPath(event.target.value)}
            placeholder="本地路径、GitHub 或 skills.sh"
            value={path}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            导入后默认启用，详情页会显示实际兼容性与可调用状态。
          </p>
          <Button className="mt-3" disabled={importing || !path.trim()} type="submit">
            <Upload aria-hidden="true" />
            {importing ? "正在导入" : "开始导入"}
          </Button>
        </form>
      </details>

      <div className="grid gap-2.5 sm:grid-cols-[minmax(0,1fr)_11rem_11rem]">
        <label className="relative block">
          <span className="sr-only">搜索 Skill 名称、说明或能力</span>
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <input
            className="min-h-10.5 w-full rounded-xl border border-border bg-card pr-3 pl-9.5 text-sm text-foreground shadow-2xs outline-none transition-all placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25"
            onChange={(event) => changeFilter(() => setKeyword(event.target.value))}
            placeholder="搜索名称、说明或能力…"
            type="search"
            value={keyword}
          />
        </label>
        <label className="relative block">
          <span className="sr-only">按状态筛选 Skill</span>
          <select
            aria-label="按状态筛选"
            className="min-h-10.5 w-full rounded-xl border border-border bg-card px-3 text-sm text-foreground shadow-2xs outline-none transition-all focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25"
            onChange={(event) => changeFilter(() => setStatus(event.target.value as StatusFilter))}
            value={status}
          >
            <option value="all">全部状态</option>
            <option value="callable">Agent 可调用</option>
            <option value="enabled">已启用但不可调用</option>
            <option value="disabled">已停用</option>
          </select>
        </label>
        <label className="relative block">
          <span className="sr-only">按来源筛选 Skill</span>
          <select
            aria-label="按来源筛选"
            className="min-h-10.5 w-full rounded-xl border border-border bg-card px-3 text-sm text-foreground shadow-2xs outline-none transition-all focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25"
            onChange={(event) => changeFilter(() => setSource(event.target.value))}
            value={source}
          >
            <option value="all">全部来源</option>
            {sources.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
      </div>

      <p aria-live="polite" className="text-xs font-medium text-muted-foreground">
        共 {filtered.length} 个 Skill
        {filtered.length !== items.length ? `（已筛选，共 ${items.length} 个）` : ""}
      </p>

      {items.length === 0 ? (
        <div className="grid min-h-56 place-items-center rounded-2xl border border-dashed bg-card text-center">
          <div>
            <Sparkles aria-hidden="true" className="mx-auto size-7 text-muted-foreground" />
            <p className="mt-3 text-sm text-muted-foreground">当前没有 Skill。</p>
            <p className="mt-1 text-xs text-muted-foreground">
              可以从上方“导入 Skill”添加本地路径、GitHub 或 skills.sh 来源。
            </p>
          </div>
        </div>
      ) : visibleItems.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {visibleItems.map((item) => (
            <button
              className="group flex h-full min-h-44 flex-col rounded-2xl border border-border/85 bg-card p-4.5 text-left shadow-2xs transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/30"
              key={item.name}
              onClick={() => onSelect(item)}
              type="button"
            >
              <div className="flex w-full items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  <span className="grid size-10 shrink-0 place-items-center rounded-xl border border-primary/15 bg-primary-soft text-primary shadow-2xs">
                    <Sparkles aria-hidden="true" className="size-4.5" />
                  </span>
                  <div className="min-w-0">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-primary">
                      Skill
                    </span>
                    <h2 className="mt-0.5 truncate text-[15px] font-semibold tracking-tight text-foreground" title={item.name}>
                      {item.name}
                    </h2>
                  </div>
                </div>
                <SkillStatusBadge callable={item.callable_by_agent} enabled={item.enabled} />
              </div>
              <p className="mt-3 text-[13px] leading-relaxed text-muted-foreground">
                {compactCapabilityText(item.description || "暂无说明", 130)}
              </p>
              <div className="mt-auto flex flex-wrap gap-1.5 border-t border-border/60 pt-3.5 text-xs text-muted-foreground">
                <span className="rounded-lg border border-border/75 bg-muted/55 px-2 py-0.5 font-medium text-secondary-foreground">{item.source}</span>
                <span className="rounded-lg border border-border/75 bg-muted/55 px-2 py-0.5 font-medium text-secondary-foreground">
                  {compatibilityLabel(item.compatibility.status)}
                </span>
                <span className="rounded-lg border border-border/75 bg-muted/55 px-2 py-0.5 font-medium text-secondary-foreground">{item.tools.length} 个工具</span>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="grid min-h-56 place-items-center rounded-2xl border border-dashed bg-card text-center">
          <div>
            <Sparkles aria-hidden="true" className="mx-auto size-7 text-muted-foreground" />
            <p className="mt-3 text-sm text-muted-foreground">没有匹配的 Skill。</p>
          </div>
        </div>
      )}

      {filtered.length > PAGE_SIZE ? (
        <nav
          aria-label="Skill 列表分页"
          className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"
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
                className="min-w-8.5 px-0"
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

function SkillStatusBadge({ callable, enabled }: { callable: boolean; enabled: boolean }) {
  const label = callable ? "Agent 可调用" : enabled ? "已启用" : "已停用";
  const className = callable
    ? "border-success/25 bg-success/10 text-success"
    : enabled
      ? "border-warning/25 bg-warning/10 text-warning"
      : "border-border bg-muted text-muted-foreground";
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${className}`}>
      <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}
