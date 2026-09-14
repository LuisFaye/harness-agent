import { PageHeader } from "@/components/layout/page-header";
import { Activity } from "lucide-react";
import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { RunDetailPanel } from "./run-detail-panel";
import { RunListPanel } from "./run-list-panel";
import { type RunFilters, useRunsWorkspace } from "./use-runs-workspace";

export function RunsPage() {
  const { runId: routeRunId = "" } = useParams();
  const runId = decodeURIComponent(routeRunId);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedFilters = useMemo(() => readFilters(searchParams), [searchParams]);
  const [draftFilters, setDraftFilters] = useState(appliedFilters);
  const workspace = useRunsWorkspace(runId, appliedFilters);

  useEffect(() => {
    setDraftFilters(appliedFilters);
  }, [
    appliedFilters.keyword,
    appliedFilters.sessionId,
    appliedFilters.status,
  ]);

  const updateSearch = useCallback((filters: RunFilters, clearSelection = false) => {
    const next = new URLSearchParams();
    if (filters.keyword) {
      next.set("keyword", filters.keyword);
    }
    if (filters.status) {
      next.set("status", filters.status);
    }
    if (filters.sessionId) {
      next.set("session_id", filters.sessionId);
    }
    if (filters.offset > 0) {
      next.set("offset", String(filters.offset));
    }
    if (clearSelection && runId) {
      navigate({
        pathname: "/runs",
        search: next.toString(),
      }, { replace: true });
      return;
    }
    setSearchParams(next, { replace: true });
  }, [navigate, runId, setSearchParams]);

  function selectRun(nextRunId: string) {
    navigate({
      pathname: `/runs/${encodeURIComponent(nextRunId)}`,
      search: searchParams.toString(),
    });
  }

  function showList() {
    navigate({ pathname: "/runs", search: searchParams.toString() });
  }

  function applyFilters() {
    const next = {
      keyword: draftFilters.keyword.trim(),
      status: draftFilters.status.trim(),
      sessionId: draftFilters.sessionId.trim(),
      offset: 0,
    };
    setDraftFilters(next);
    updateSearch(next, true);
  }

  function resetFilters() {
    const empty = { keyword: "", status: "", sessionId: "", offset: 0 };
    setDraftFilters(empty);
    updateSearch(empty, true);
  }

  return (
    <section
      aria-labelledby="runs-page-title"
      className="flex h-full min-h-0 flex-col p-4 sm:p-6"
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border bg-card">
        <div className="shrink-0 border-b px-5 py-5">
          <PageHeader title="运行监控" description="查看运行状态、任务结果和执行过程。" icon={Activity} id="runs-page-title">
          <Button disabled={workspace.listFetching} onClick={() => void workspace.refresh()} variant="outline">
            <RefreshCw aria-hidden="true" className={workspace.listFetching ? "animate-spin" : ""} />
            刷新
          </Button>
          </PageHeader>
        </div>

        <div
          className={cn(
            "grid min-h-0 flex-1 overflow-hidden",
            runId && "lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]",
          )}
        >
          <div className={cn("min-h-0", runId && "hidden lg:block")}>
            <RunListPanel
              error={workspace.listError}
              fetching={workspace.listFetching}
              filters={draftFilters}
              hasNext={workspace.pagination.hasNext}
              hasPrevious={workspace.pagination.hasPrevious}
              items={workspace.items}
              loading={workspace.listLoading}
              onApplyFilters={applyFilters}
              onFilterChange={setDraftFilters}
              onNext={() => updateSearch({
                ...appliedFilters,
                offset: appliedFilters.offset + workspace.pagination.pageSize,
              }, true)}
              onPrevious={() => updateSearch({
                ...appliedFilters,
                offset: Math.max(0, appliedFilters.offset - workspace.pagination.pageSize),
              }, true)}
              onResetFilters={resetFilters}
              onSelect={selectRun}
              pageEnd={workspace.pagination.pageEnd}
              pageStart={workspace.pagination.pageStart}
              selectedRunId={runId}
              total={workspace.total}
            />
          </div>
          {runId ? (
            <div className="min-h-0 border-t lg:border-t-0 lg:border-l">
              <RunDetailPanel
                cancelError={workspace.cancelError}
                cancelPending={workspace.cancelPending}
                detail={workspace.detail}
                error={workspace.detailError}
                fetching={workspace.detailFetching}
                loading={workspace.detailLoading}
                onBack={showList}
                onCancel={() => void workspace.cancelRun()}
                onRefresh={() => void workspace.refresh()}
                runId={runId}
              />
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function readFilters(searchParams: URLSearchParams): RunFilters {
  const offset = Number(searchParams.get("offset") || 0);
  return {
    keyword: searchParams.get("keyword") || "",
    status: searchParams.get("status") || "",
    sessionId: searchParams.get("session_id") || "",
    offset: Number.isInteger(offset) && offset >= 0 ? offset : 0,
  };
}
