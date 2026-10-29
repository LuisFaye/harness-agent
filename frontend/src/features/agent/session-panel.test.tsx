// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SessionSummary } from "@/lib/api/schemas";

import { SessionPanel } from "./session-panel";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const sessions: SessionSummary[] = [
  {
    session_id: "session-1",
    title: "项目检查",
    preview: "检查已经完成",
    message_count: 2,
    last_message_at: "2026-07-29T08:00:00Z",
    has_summary: false,
  },
];

describe("会话侧栏", () => {
  it("搜索输入防抖 250ms 后交给服务端并在换词时重置关键字", () => {
    vi.useFakeTimers();
    const onKeywordChange = vi.fn();
    renderPanel({ onKeywordChange });

    expect(screen.queryByRole("searchbox", { name: "搜索会话" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "搜索会话" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索会话" }), {
      target: { value: " 项目 " },
    });
    act(() => vi.advanceTimersByTime(249));
    expect(onKeywordChange).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(onKeywordChange).toHaveBeenLastCalledWith("项目");

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索会话" }), {
      target: { value: "新关键字" },
    });
    act(() => vi.advanceTimersByTime(250));
    expect(onKeywordChange).toHaveBeenLastCalledWith("新关键字");
  });

  it("标记当前会话并提供可键盘操作的加载更多兜底", () => {
    const onLoadMore = vi.fn();
    renderPanel({ hasMore: true, onLoadMore });

    expect(screen.getByRole("button", { name: /项目检查/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    const loadMore = screen.getByRole("button", { name: "加载更多" });
    loadMore.focus();
    fireEvent.keyDown(loadMore, { key: "Enter" });
    fireEvent.click(loadMore);
    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it("加载中禁用重复分页请求", () => {
    renderPanel({ hasMore: true, loadingMore: true });
    expect(screen.getByRole("button", { name: "正在加载…" })).toBeDisabled();
  });
});

function renderPanel(overrides: Partial<Parameters<typeof SessionPanel>[0]> = {}) {
  return render(<SessionPanel
    activeSessionId="session-1"
    error={null}
    hasMore={false}
    keyword=""
    loading={false}
    loadingMore={false}
    onKeywordChange={vi.fn()}
    onLoadMore={vi.fn()}
    onNew={vi.fn()}
    onSelect={vi.fn()}
    sessions={sessions}
    {...overrides}
  />);
}
