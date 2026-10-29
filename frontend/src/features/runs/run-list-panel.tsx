import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import type { FormEvent } from "react";

import { Button } from "@/components/ui/button";
import type { RunSummary } from "@/lib/api/schemas";
import { cn } from "@/lib/utils";

import { formatRunDate } from "./run-format";
import { RunStatusBadge } from "./run-status-badge";
import type { RunFilters } from "./use-runs-workspace";

interface RunListPanelProps {
  items: RunSummary[];
  selectedRunId: string;
  filters: RunFilters;
  total: number;
  pageStart: number;
  pageEnd: number;
  loading: boolean;
  fetching: boolean;
  error: unknown;
  hasPrevious: boolean;
  hasNext: boolean;
  onFilterChange: (filters: RunFilters) => void;
  onApplyFilters: () => void;
  onResetFilters: () => void;
  onSelect: (runId: string) => void;
  onPrevious: () => void;
  onNext: () => void;
}

export function RunListPanel({
  items,
  selectedRunId,
  filters,
  total,
  pageStart,
  pageEnd,
  loading,
  fetching,
  error,
  hasPrevious,
  hasNext,
  onFilterChange,
  onApplyFilters,
  onResetFilters,
  onSelect,
  onPrevious,
  onNext,
}: RunListPanelProps) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onApplyFilters();
  }

  return (
    <aside
      aria-label="Agent 运行列表"
      className="flex h-full min-h-0 flex-col overflow-hidden bg-card"
    >
      <form className="grid gap-3 border-b border-border/75 bg-muted/15 p-3.5" onSubmit={submit}>
        <label className="grid gap-1 text-xs font-medium text-muted-foreground">
          搜索运行
          <span className="relative">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground/80"
            />
            <input
              className="h-9.5 w-full rounded-xl border border-border/85 bg-background pr-3 pl-9 text-sm text-foreground shadow-2xs outline-none transition-colors focus-visible:border-primary/40 focus-visible:ring-2 focus-visible:ring-ring/30"
              onChange={(event) => onFilterChange({ ...filters, keyword: event.target.value })}
              placeholder="run_id 或用户输入"
              type="search"
              value={filters.keyword}
            />
          </span>
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="grid gap-1 text-xs font-medium text-muted-foreground">
            状态
            <select
              className="h-9.5 rounded-xl border border-border/85 bg-background px-3 text-sm text-foreground shadow-2xs outline-none transition-colors focus-visible:border-primary/40 focus-visible:ring-2 focus-visible:ring-ring/30"
              onChange={(event) => onFilterChange({ ...filters, status: event.target.value })}
              value={filters.status}
            >
              <option value="">全部状态</option>
              <option value="queued">排队中</option>
              <option value="running">运行中</option>
              <option value="success">成功</option>
              <option value="error">失败</option>
              <option value="needs_input">等待补充</option>
              <option value="needs_confirmation">等待确认</option>
              <option value="max_iterations">达到轮次上限</option>
              <option value="cancel_requested">正在取消</option>
              <option value="cancelled">已取消</option>
            </select>
          </label>
          <label className="grid gap-1 text-xs font-medium text-muted-foreground">
            会话 ID
            <input
              className="h-9.5 min-w-0 rounded-xl border border-border/85 bg-background px-3 text-sm text-foreground shadow-2xs outline-none transition-colors focus-visible:border-primary/40 focus-visible:ring-2 focus-visible:ring-ring/30"
              onChange={(event) => onFilterChange({ ...filters, sessionId: event.target.value })}
              placeholder="精确匹配"
              type="text"
              value={filters.sessionId}
            />
          </label>
        </div>
        <div className="flex items-center justify-between gap-2 pt-0.5">
          <Button size="sm" type="submit">应用筛选</Button>
          <Button onClick={onResetFilters} size="sm" type="button" variant="ghost">
            <X aria-hidden="true" />
            清空
          </Button>
        </div>
      </form>

      <div aria-live="polite" className="flex items-center justify-between px-3.5 py-2 text-xs text-muted-foreground">
        <span>{total ? `共 ${total} 次 · ${pageStart}-${pageEnd}` : "暂无运行"}</span>
        {fetching && !loading ? <span className="text-primary">正在刷新</span> : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-2.5" role="listbox" aria-label="Agent 运行">
        {loading ? <RunListSkeleton /> : null}
        {error ? (
          <div className="m-2 rounded-xl border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive" role="alert">
            运行列表加载失败，请检查运行存储后重试。
          </div>
        ) : null}
        {!loading && !error && !items.length ? (
          <div className="px-4 py-12 text-center text-sm text-muted-foreground">
            当前筛选下没有运行记录。
          </div>
        ) : null}
        {items.map((item) => {
          const selected = item.run_id === selectedRunId;
          return (
            <button
              aria-selected={selected}
              className={cn(
                "mb-1.5 grid min-h-26 w-full gap-1.5 rounded-xl border border-border/55 bg-background/50 px-3.5 py-3 text-left transition-all duration-150",
                "hover:border-primary/25 hover:bg-card hover:shadow-2xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                selected && "border-primary/35 bg-primary-soft/65 shadow-2xs",
              )}
              key={item.run_id}
              onClick={() => onSelect(item.run_id)}
              role="option"
              type="button"
            >
              <span className="flex items-center justify-between gap-2">
                <RunStatusBadge status={item.status} />
                <time className="text-[11px] text-muted-foreground">
                  {formatRunDate(item.finished_at || item.started_at)}
                </time>
              </span>
              <strong className="line-clamp-2 text-[13.5px] font-medium leading-snug text-foreground">
                {item.query_summary || "未保存用户输入"}
              </strong>
              <span className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                <code className="truncate font-mono">{item.run_id}</code>
                <span className="truncate">{item.model_id || "模型未知"}</span>
              </span>
            </button>
          );
        })}
      </div>

      <nav aria-label="运行列表翻页" className="flex items-center justify-between border-t border-border/75 bg-muted/15 p-3">
        <Button disabled={!hasPrevious || fetching} onClick={onPrevious} size="sm" variant="outline">
          <ChevronLeft aria-hidden="true" />
          上一页
        </Button>
        <Button disabled={!hasNext || fetching} onClick={onNext} size="sm" variant="outline">
          下一页
          <ChevronRight aria-hidden="true" />
        </Button>
      </nav>
    </aside>
  );
}

function RunListSkeleton() {
  return (
    <div aria-label="正在加载运行列表" className="space-y-2 p-2" role="status">
      {[0, 1, 2, 3].map((item) => (
        <div className="h-28 animate-pulse rounded-lg bg-muted" key={item} />
      ))}
    </div>
  );
}
