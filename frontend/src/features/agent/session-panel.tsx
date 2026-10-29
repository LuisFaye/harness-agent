import { LoaderCircle, MessageSquarePlus, Search, SlidersHorizontal, Star, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import type { SessionSummary } from "@/lib/api/schemas";
import type { SessionTimeFilter } from "./session-time-filter";

interface SessionPanelProps {
  sessions: SessionSummary[];
  activeSessionId: string;
  timeFilter?: SessionTimeFilter;
  totalSessions?: number;
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  keyword: string;
  error: unknown;
  onKeywordChange: (keyword: string) => void;
  onTimeFilterChange?: (filter: SessionTimeFilter) => void;
  onLoadMore: () => void;
  onSelect: (sessionId: string) => void;
  onNew?: () => void;
  favoriteSessionIds?: string[];
  runningSessionIds?: string[];
  completedSessionIds?: string[];
  className?: string;
}

export function SessionPanel({
  sessions,
  activeSessionId,
  timeFilter = "all",
  totalSessions = 0,
  loading,
  loadingMore,
  hasMore,
  keyword,
  error,
  onKeywordChange,
  onTimeFilterChange = () => {},
  onLoadMore,
  onSelect,
  onNew,
  favoriteSessionIds = [],
  runningSessionIds = [],
  completedSessionIds = [],
  className,
}: SessionPanelProps) {
  const [searchInput, setSearchInput] = useState(keyword);
  const [searchOpen, setSearchOpen] = useState(Boolean(keyword));
  const [filterOpen, setFilterOpen] = useState(false);
  const searchId = useId();
  const loadSentinelRef = useRef<HTMLDivElement | null>(null);
  const orderedSessions = useMemo(() => {
    if (!favoriteSessionIds.length) {
      return sessions;
    }
    const favoriteSet = new Set(favoriteSessionIds);
    return [...sessions].sort((left, right) => {
      const leftFavorite = favoriteSet.has(left.session_id) ? 0 : 1;
      const rightFavorite = favoriteSet.has(right.session_id) ? 0 : 1;
      return leftFavorite - rightFavorite;
    });
  }, [favoriteSessionIds, sessions]);

  useEffect(() => {
    const timer = window.setTimeout(() => onKeywordChange(searchInput.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [onKeywordChange, searchInput]);

  useEffect(() => {
    const sentinel = loadSentinelRef.current;
    if (!sentinel || !hasMore || loadingMore || typeof IntersectionObserver === "undefined") {
      return;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) {
        onLoadMore();
      }
    }, { rootMargin: "160px" });
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, loadingMore, onLoadMore]);

  return (
    <Sidebar
      aria-label="最近对话"
      className={`shell-session-panel ${className ?? ""}`}
      role="region"
    >
      <SidebarHeader>
        {onNew ? (
          <Button className="w-full justify-start" onClick={onNew}>
            <MessageSquarePlus aria-hidden="true" />
            新建对话
          </Button>
        ) : null}
        <div className="flex items-center justify-between gap-2">
          <h2 className="flex min-w-0 items-center gap-1.5 truncate pl-2 text-xs font-semibold tracking-tight text-muted-foreground">
            <span>最近对话</span>
            <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-sidebar-accent/85 px-1.5 py-0.5 text-[11px] font-semibold text-sidebar-accent-foreground tabular-nums">
              {totalSessions}
            </span>
          </h2>
          <div className="flex items-center gap-0.5">
            <Button
              aria-label={searchOpen || keyword ? "关闭并清除会话搜索" : "搜索会话"}
              aria-expanded={searchOpen || Boolean(keyword)}
              title="搜索会话"
              size="icon-sm"
              variant="ghost"
              className={searchOpen || keyword ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-muted-foreground"}
              onClick={() => {
                if (searchOpen || keyword) {
                  setSearchInput("");
                  onKeywordChange("");
                  setSearchOpen(false);
                } else setSearchOpen(true);
              }}
            >
              <Search aria-hidden="true" className="size-3.5" />
            </Button>
            <Popover open={filterOpen} onOpenChange={setFilterOpen}>
              <PopoverTrigger
                render={
                  <Button
                    aria-label="筛选会话"
                    title="筛选会话"
                    size="icon-sm"
                    variant="ghost"
                    className={timeFilter !== "all" ? "bg-primary-soft text-primary" : "text-muted-foreground"}
                  />
                }
              >
                <SlidersHorizontal aria-hidden="true" className="size-3.5" />
              </PopoverTrigger>
              <PopoverContent side="bottom" align="end" className="w-56 rounded-2xl border-border p-3 shadow-lg">
                <p className="mb-2 text-xs font-semibold text-muted-foreground">会话时间范围</p>
                <div aria-label="会话时间范围" className="grid grid-cols-2 gap-1" role="group">
                  {([["all", "全部"], ["today", "今天"], ["yesterday", "昨天"], ["7d", "7天内"]] as const).map(([value, label]) => (
                    <Button
                      key={value}
                      size="sm"
                      aria-pressed={timeFilter === value}
                      variant={timeFilter === value ? "secondary" : "ghost"}
                      onClick={() => {
                        onTimeFilterChange(value);
                        setFilterOpen(false);
                      }}
                    >
                      {label}
                    </Button>
                  ))}
                </div>
              </PopoverContent>
            </Popover>
          </div>
        </div>
        {timeFilter !== "all" ? (
          <button
            type="button"
            className="mx-1.5 flex min-h-7 items-center gap-1.5 self-start rounded-full border border-primary/20 bg-primary-soft px-2.5 py-0.5 text-xs font-medium text-primary transition-colors hover:bg-primary-soft/80"
            onClick={() => onTimeFilterChange("all")}
            aria-label="清除会话时间筛选"
          >
            {timeFilter === "today" ? "今天" : timeFilter === "yesterday" ? "昨天" : "7天内"}
            <X aria-hidden="true" className="size-3" />
          </button>
        ) : null}
        {searchOpen || keyword ? (
          <div className="relative pt-0.5">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground"
            />
            <input
              aria-label="搜索会话"
              autoFocus
              className="min-h-9.5 w-full rounded-xl border border-sidebar-border bg-card/90 pr-3 pl-8.5 text-xs text-foreground shadow-2xs outline-none transition-all placeholder:text-muted-foreground focus-visible:border-ring focus-visible:bg-card focus-visible:ring-2 focus-visible:ring-ring/25"
              id={searchId}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="搜索标题或内容…"
              type="search"
              value={searchInput}
            />
          </div>
        ) : null}
      </SidebarHeader>

      <SidebarContent>
        {loading ? <SessionSkeleton /> : null}
        {error ? (
          <p className="rounded-xl border border-destructive/20 bg-destructive/10 p-3 text-xs text-destructive" role="alert">
            会话列表加载失败，请稍后重试。
          </p>
        ) : null}
        {!loading && !error && sessions.length === 0 ? (
          <p className="px-3 py-8 text-center text-xs text-muted-foreground">
            {keyword ? "没有匹配的会话" : "还没有历史对话"}
          </p>
        ) : null}
        <SidebarMenu>
          {orderedSessions.map((session) => {
            const isFavorite = favoriteSessionIds.includes(session.session_id);
            return (
              <SidebarMenuItem key={session.session_id}>
                <SidebarMenuButton
                  title={session.title || "未命名对话"}
                  isActive={session.session_id === activeSessionId}
                  onClick={() => onSelect(session.session_id)}
                >
                  <span className="flex w-full min-w-0 items-center justify-between gap-2 text-left">
                    <strong className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-[12.5px] font-medium leading-snug">
                      {isFavorite ? (
                        <Star
                          aria-hidden="true"
                          className="size-3 shrink-0 fill-current text-warning"
                        />
                      ) : null}
                      <span className="truncate">{session.title || "未命名对话"}</span>
                      {runningSessionIds.includes(session.session_id) ? (
                        <span className="inline-flex shrink-0 items-center">
                          <LoaderCircle
                            aria-hidden="true"
                            className="size-3 animate-spin text-primary motion-reduce:animate-none"
                          />
                          <span className="sr-only">Agent 正在处理</span>
                        </span>
                      ) : completedSessionIds.includes(session.session_id) ? (
                        <span className="inline-flex size-3 shrink-0 items-center justify-center">
                          <span
                            aria-hidden="true"
                            className="size-1.5 rounded-full bg-success ring-2 ring-success/25"
                          />
                          <span className="sr-only">Agent 已完成，尚未查看</span>
                        </span>
                      ) : null}
                      {isFavorite ? <span className="sr-only">本地收藏</span> : null}
                    </strong>
                    <time className="shrink-0 text-[10.5px] text-muted-foreground/70 tabular-nums">
                      {formatSessionTime(session.last_message_at)}
                    </time>
                  </span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
        <div ref={loadSentinelRef} />
        {hasMore ? (
          <Button
            className="mt-1.5 w-full text-xs text-muted-foreground"
            disabled={loadingMore}
            onClick={onLoadMore}
            size="sm"
            variant="ghost"
          >
            {loadingMore ? "正在加载…" : "加载更多"}
          </Button>
        ) : null}
      </SidebarContent>
    </Sidebar>
  );
}

function formatSessionTime(value: string | null): string {
  if (!value) {
    return "暂无";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const now = new Date();
  const sameDay = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  if (sameDay) {
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  }
  if (date.getFullYear() === now.getFullYear()) {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
    }).format(date);
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function SessionSkeleton() {
  return (
    <div aria-label="正在加载会话" className="space-y-2 p-1" role="status">
      {[0, 1, 2].map((item) => (
        <div className="h-11 animate-pulse rounded-lg bg-muted" key={item} />
      ))}
    </div>
  );
}
