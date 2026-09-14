// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/App";
import { readTextFixture } from "@/test/fixture-loader";

const matchMedia = vi.fn().mockImplementation((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  addListener: vi.fn(),
  removeListener: vi.fn(),
  dispatchEvent: vi.fn(),
}));

describe("React 工作台基础壳层", () => {
  beforeEach(() => {
    window.location.hash = "#/agent";
    window.localStorage.clear();
    document.documentElement.className = "";
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: matchMedia,
    });
    vi.stubGlobal("fetch", createApiFetchMock());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("提供真实聊天工作区和明确的阶段状态", async () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "新对话" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "跳到主要内容" })).toHaveAttribute(
      "href",
      "#workspace-main",
    );
    expect(await screen.findByText("开始一个真实任务")).toBeInTheDocument();
    expect(screen.getByLabelText("发送给 SB Agent")).toBeInTheDocument();
  });

  it("可以切换路由和主题", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("link", { name: "运行监控" }));
    expect(await screen.findByRole("heading", { name: "运行监控" })).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "切换到深色主题" })[0]!);
    expect(document.documentElement).toHaveClass("dark");
    expect(window.localStorage.getItem("sb-agent-theme")).toBe("dark");
  });

  it("管理路由加载真实页面而不是占位内容", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("link", { name: "自动任务" }));
    expect(await screen.findByRole("heading", { name: "自动任务" })).toBeInTheDocument();
    expect(await screen.findByText("暂无自动任务")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "能力中心" }));
    expect(await screen.findByRole("heading", { name: "能力中心" })).toBeInTheDocument();
    expect(await screen.findByText("当前没有可注册的 Tool。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "设置" }));
    expect(await screen.findByRole("heading", { name: "设置" })).toBeInTheDocument();
    expect(await screen.findByDisplayValue("fixture-model")).toBeInTheDocument();
  });

  it("发送消息后消费真实 NDJSON 契约并显示最终 Markdown", async () => {
    render(<App />);

    const composer = screen.getByLabelText("发送给 SB Agent");
    fireEvent.change(composer, { target: { value: "查询成都天气" } });
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter" });

    expect(await screen.findByText(
      "成都当前 28°C。",
      {},
      { timeout: 3000 },
    )).toBeInTheDocument();
    await waitFor(() => {
      expect(composer).toBeEnabled();
    });
    expect(screen.queryByText(/工具调用 \d+ 次 · 上限 \d+ 次/)).not.toBeInTheDocument();
  });

  it("移动端导航抽屉关闭后把焦点还给触发按钮", async () => {
    render(<App />);
    const trigger = screen.getByRole("button", { name: "打开工作台导航" });

    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it("对话页把会话列表放在左侧栏", async () => {
    render(<App />);

    expect(await screen.findByRole("searchbox", { name: "搜索会话" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建对话" })).toBeInTheDocument();
    expect(screen.getByText("最近对话")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "收起会话列表" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开会话列表" })).not.toBeInTheDocument();
  });
});

function createApiFetchMock() {
  let completed = false;
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/ask/stream")) {
      const requestPayload = init?.body
        ? JSON.parse(String(init.body)) as { session_id?: string }
        : {};
      completed = Boolean(requestPayload.session_id);
      return new Response(readTextFixture("stream-success.ndjson"), {
        status: 200,
        headers: {
          "Content-Type": "application/x-ndjson",
          "X-Run-Id": "ask-fixture-success",
        },
      });
    }
    if (url.includes("/sessions?")) {
      return jsonResponse({ items: [], total: 0, limit: 30, offset: 0 });
    }
    if (url.includes("/schedules?")) {
      return jsonResponse({ items: [] });
    }
    if (url.endsWith("/tools")) {
      return jsonResponse({ items: [], total: 0 });
    }
    if (url.endsWith("/skills")) {
      return jsonResponse({ items: [] });
    }
    if (url.endsWith("/mcp/servers")) {
      return jsonResponse([]);
    }
    if (url.endsWith("/settings/model/available")) {
      return jsonResponse({ status: "ok", models: ["fixture-model", "fixture-model-alt"], duration_ms: 12 });
    }
    if (url.endsWith("/settings/model")) {
      const values = {
        provider: "openai_compatible",
        model_id: "fixture-model",
        base_url: "https://example.test/v1",
        api_key_configured: true,
        agno_telemetry: false,
        thinking_enabled: true,
        reasoning_effort: "high",
        context_token_budget: 4000,
      };
      return jsonResponse({
        config_path: "/tmp/config.toml",
        config_exists: true,
        config: values,
        effective: values,
        env_overrides: {},
        redacted_config: {},
      });
    }
    if (url.includes("/attachments?")) {
      return jsonResponse({ items: [] });
    }
    if (url.includes("/runs?")) {
      return jsonResponse({ items: [], total: 0, limit: 1, offset: 0 });
    }
    if (url.includes("/sessions/")) {
      const sessionId = decodeURIComponent(url.split("/sessions/")[1]?.split("?")[0] ?? "session-test");
      return jsonResponse({
        session_id: sessionId,
        messages: completed
          ? [
            {
              id: 1,
              session_id: sessionId,
              role: "user",
              content: "查询成都天气",
              created_at: "2026-07-28T10:00:00+08:00",
              run_id: "ask-fixture-success",
            },
            {
              id: 2,
              session_id: sessionId,
              role: "assistant",
              content: "成都当前 28°C。",
              created_at: "2026-07-28T10:00:01+08:00",
              run_id: "ask-fixture-success",
            },
          ]
          : [],
        summary: null,
        context_observation: null,
      });
    }
    return jsonResponse({});
  });
}

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
