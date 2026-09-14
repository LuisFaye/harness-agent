// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { listSessions } = vi.hoisted(() => ({
  listSessions: vi.fn(async (
    _userId: string,
    filters: { keyword?: string; limit?: number; offset?: number },
  ) => ({
  items: Array.from(
    { length: filters.keyword ? 1 : (filters.offset === 0 ? 30 : 1) },
    (_, index) => ({
    session_id: `${filters.keyword || "recent"}-${(filters.offset ?? 0) + index}`,
    title: "会话",
    preview: "预览",
    message_count: 1,
    last_message_at: null,
    has_summary: false,
    }),
  ),
  total: filters.keyword ? 1 : 31,
  limit: filters.limit ?? 30,
  offset: filters.offset ?? 0,
  })),
}));

vi.mock("@/lib/api/agent", () => ({
  cancelRun: vi.fn(),
  getSession: vi.fn(async (_userId: string, sessionId: string) => ({
    session_id: sessionId,
    messages: [],
    summary: null,
    context_observation: null,
  })),
  listSessionAttachments: vi.fn(async () => ({ items: [] })),
  listSessionArtifacts: vi.fn(async () => ({ items: [], total: 0, limit: 100, offset: 0 })),
  listSessionRuns: vi.fn(async () => ({
    items: [],
    total: 0,
    limit: 1,
    offset: 0,
  })),
  listSessions,
  uploadSessionAttachment: vi.fn(),
}));

vi.mock("@/lib/stream/client", () => ({
  isTerminalProjection: vi.fn(() => false),
  recoverAgentRun: vi.fn(),
  streamAgentRun: vi.fn(),
}));

import { useAgentWorkspace } from "./use-agent-workspace";

afterEach(() => {
  listSessions.mockClear();
  window.localStorage.clear();
});

describe("会话无限分页", () => {
  it("加载下一页使用当前 offset，换搜索词后从第一页重新查询", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    const { result, rerender } = renderHook(
      ({ keyword }) => useAgentWorkspace("session-current", keyword),
      { initialProps: { keyword: "" }, wrapper },
    );

    await waitFor(() => expect(result.current.sessionsLoading).toBe(false));
    expect(listSessions).toHaveBeenCalledWith("default", {
      keyword: "",
      limit: 30,
      offset: 0,
    });

    await act(async () => {
      await result.current.loadNextSessionsPage();
    });
    expect(listSessions).toHaveBeenLastCalledWith("default", {
      keyword: "",
      limit: 30,
      offset: 30,
    });

    rerender({ keyword: "项目" });
    await waitFor(() => expect(result.current.sessions[0]?.session_id).toBe("项目-0"));
    expect(listSessions).toHaveBeenLastCalledWith("default", {
      keyword: "项目",
      limit: 30,
      offset: 0,
    });
  });
});
