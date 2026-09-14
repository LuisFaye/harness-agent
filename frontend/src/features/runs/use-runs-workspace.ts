import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";

import { cancelRun, getRun, listRuns } from "@/lib/api/agent";
import { useAuth } from "@/features/auth/auth-provider";

const PAGE_SIZE = 20;
const ACTIVE_STATUSES = new Set(["queued", "running", "cancel_requested"]);

export interface RunFilters {
  keyword: string;
  status: string;
  sessionId: string;
  offset: number;
}

export function useRunsWorkspace(runId: string, filters: RunFilters) {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const userId = user!.id;
  const listQuery = useQuery({
    queryKey: ["runs", userId, filters],
    queryFn: () => listRuns(userId, {
      keyword: filters.keyword,
      status: filters.status,
      sessionId: filters.sessionId,
      limit: PAGE_SIZE,
      offset: filters.offset,
    }),
    refetchInterval: (query) => (
      query.state.data?.items.some((item) => ACTIVE_STATUSES.has(item.status))
        ? 3000
        : 5000
    ),
  });
  const detailQuery = useQuery({
    queryKey: ["run", userId, runId],
    queryFn: () => getRun(userId, runId),
    enabled: Boolean(runId),
    refetchInterval: (query) => (
      query.state.data && ACTIVE_STATUSES.has(query.state.data.status)
        ? 2000
        : false
    ),
  });
  const cancelMutation = useMutation({
    mutationFn: (targetRunId: string) => cancelRun(userId, targetRunId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["runs", userId] }),
        queryClient.invalidateQueries({ queryKey: ["run", userId, runId] }),
      ]);
    },
  });

  const refresh = useCallback(async () => {
    await Promise.all([
      listQuery.refetch(),
      runId ? detailQuery.refetch() : Promise.resolve(),
    ]);
  }, [detailQuery, listQuery, runId]);

  const result = listQuery.data;
  const pagination = useMemo(() => ({
    pageStart: result?.total ? filters.offset + 1 : 0,
    pageEnd: result ? Math.min(result.total, filters.offset + result.items.length) : 0,
    hasPrevious: filters.offset > 0,
    hasNext: Boolean(result && filters.offset + PAGE_SIZE < result.total),
    pageSize: PAGE_SIZE,
  }), [filters.offset, result]);

  return {
    items: result?.items ?? [],
    total: result?.total ?? 0,
    listLoading: listQuery.isLoading,
    listFetching: listQuery.isFetching,
    listError: listQuery.error,
    detail: detailQuery.data ?? null,
    detailLoading: detailQuery.isLoading,
    detailFetching: detailQuery.isFetching,
    detailError: detailQuery.error,
    pagination,
    refresh,
    cancelRun: async () => {
      if (runId) {
        await cancelMutation.mutateAsync(runId);
      }
    },
    cancelPending: cancelMutation.isPending,
    cancelError: cancelMutation.error,
  };
}
