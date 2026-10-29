// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";

import { createRunProjection } from "@/lib/stream/run-projection";
import type { TraceItem } from "@/lib/stream/schemas";

import { MessageResponse } from "@/components/ai-elements/message-response";
import { AgentComposer } from "./agent-composer";
import { ExecutionTimeline } from "./execution-timeline";
import { MessageScroller } from "./message-scroller";
import { RunInteractionCard } from "./run-interaction-card";
import { WorkingStatusBar } from "./working-status-bar";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderComposer(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const payload = url.includes("/tools")
      ? { items: [], total: 0 }
      : { items: [] };
    return new Response(JSON.stringify(payload), {
      headers: { "Content-Type": "application/json" },
    });
  }));
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

vi.mock("@/features/auth/auth-provider", () => ({
  useAuth: () => ({ user: { id: "test-user" } }),
}));

describe("Agent 核心聊天组件", () => {
  it("实时 Markdown 支持 GFM 且不执行原始 HTML", () => {
    const { container } = render(
      <MessageResponse>
        {"| 项目 | 状态 |\n|---|---|\n| 测试 | **通过** |\n\n<img src=x onerror=alert(1)>"}
      </MessageResponse>,
    );

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("通过")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("Image blocked");
    expect(container.innerHTML).not.toContain("onerror");
  });

  it("单个波浪号范围不误判为删除线，双波浪号仍渲染删除线", () => {
    const { container } = render(
      <MessageResponse>
        {"| 区域 | 10/29 | 10/30 |\n|---|---|---|\n| 甘孜 | 大部晴 3.5~17.8°C | 阴 4.2~19.9°C |\n\n~~已作废行程~~"}
      </MessageResponse>,
    );

    const dels = container.querySelectorAll("del");
    expect(dels).toHaveLength(1);
    expect(dels[0]?.textContent).toBe("已作废行程");
    expect(container.textContent).toContain("3.5~17.8°C");
    expect(container.textContent).toContain("4.2~19.9°C");
  });

  it("流式 Markdown 可以渐进渲染未闭合的强调和代码块", () => {
    const { container } = render(
      <>
        <MessageResponse streaming>{"正在生成 **重要结论"}</MessageResponse>
        <MessageResponse streaming>{"```ts\nconst answer = 42"}</MessageResponse>
      </>,
    );

    expect(container.textContent).toContain("重要结论");
    expect(container.querySelector("pre")).not.toBeNull();
    expect(container.textContent).toContain("const answer = 42");
  });

  it("Composer 正确区分 Enter、Shift Enter 和中文输入法组合", () => {
    const onSend = vi.fn(async () => undefined);
    renderComposer(
      <AgentComposer
        isRunning={false}
        isUploading={false}
        onRemoveAttachment={vi.fn()}
        onSend={onSend}
        onStop={vi.fn(async () => undefined)}
        onUpload={vi.fn(async () => undefined)}
        pendingAttachments={[]}
        sessionId="session-test"
      />,
    );
    const textarea = screen.getByLabelText("发送给 SB Agent");
    fireEvent.change(textarea, { target: { value: "中文任务" } });
    fireEvent.compositionStart(textarea);
    fireEvent.keyDown(textarea, { key: "Enter", keyCode: 229, isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.compositionEnd(textarea);
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("中文任务");
  });

  it("高风险确认提供明确且可禁用的确认和取消动作", () => {
    const projection = {
      ...createRunProjection({ runId: "ask-confirm" }),
      status: "waiting_confirmation" as const,
    };
    render(
      <RunInteractionCard
        busy
        errorMessage={null}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
        onDismissError={vi.fn()}
        onRetry={vi.fn()}
        projection={projection}
      />,
    );

    expect(screen.getByRole("group", { name: "高风险操作确认" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
  });

  it("数据库安全输入使用显式标签且密码只交给提交回调", async () => {
    const onDatabaseConnect = vi.fn(async () => undefined);
    const projection = {
      ...createRunProjection({ runId: "ask-database" }),
      status: "waiting_input" as const,
      interaction: {
        type: "database_connection" as const,
        request_id: "dbi-test",
        title: "连接 MySQL",
        description: "密码不会发送给模型。",
        name: "mysql-front",
        host: "db.local",
        port: 3306,
        database: "agent_front",
        username: "agent",
        scope: "agent" as const,
      },
    };
    render(
      <RunInteractionCard
        busy={false}
        errorMessage={null}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
        onDatabaseConnect={onDatabaseConnect}
        onDismissError={vi.fn()}
        onRetry={vi.fn()}
        projection={projection}
      />,
    );

    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "local-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并测试连接" }));

    await waitFor(() => expect(onDatabaseConnect).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "mysql-front",
        password: "local-secret",
      }),
      "dbi-test",
    ));
    expect(screen.getByText(/不会进入模型、聊天记录或事件流/)).toBeInTheDocument();
  });

  it("流程默认单行紧凑展示，展开后只呈现自然语言足迹而不暴露工具 JSON 细节", () => {
    const items: TraceItem[] = [
      {
        key: "iteration-1-started",
        stage: "iteration_started",
        status: "running",
        title: "第 1 轮：选择动作",
        summary: "决定调用天气工具",
        details: { iteration: 1, tool: "weather_query", name: "查询天气" },
      },
      {
        key: "tool-1-finished",
        stage: "tool_finished",
        status: "success",
        title: "查询天气",
        summary: "查询天气执行完成",
        details: {
          tool: "weather_query",
          inputs: { city: "成都" },
          output_summary: "成都当前 28°C",
          duration_ms: 320,
          iteration: 1,
        },
      },
    ];

    render(<ExecutionTimeline active={false} items={items} />);

    const trigger = screen.getByRole("button", { name: /思考过程/ });
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);
    expect(screen.getByText("查询天气")).toBeInTheDocument();
    expect(screen.queryByText("weather_query")).not.toBeInTheDocument();
    expect(screen.queryByText("(320ms)")).not.toBeInTheDocument();
    expect(screen.queryByText("决定调用天气工具")).not.toBeInTheDocument();
  });

  it("运行中保持单行实时流转并预览最新思考片段，结束后定格为简短摘要", () => {
    const running: TraceItem = {
      key: "reasoning-1",
      stage: "reasoning",
      status: "running",
      title: "分析",
      summary: "",
      details: { reasoning_content: "正在检查可用能力" },
    };
    const { rerender } = render(<ExecutionTimeline active items={[running]} />);

    const trigger = screen.getByRole("button", { name: /思考过程/ });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("正在思考")).toBeVisible();
    expect(screen.getByText(/正在检查可用能力/)).toBeInTheDocument();
    expect(document.querySelector(".shimmer-text")).toBeInTheDocument();

    rerender(<ExecutionTimeline active={false} items={[{ ...running, status: "success" }]} />);
    expect(screen.getByRole("button", { name: /思考过程：已思考/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("思考没有公开摘要时展开后直接展示安全占位（无二级折叠）", () => {
    render(<ExecutionTimeline active={false} items={[{
      key: "reasoning-empty",
      stage: "reasoning",
      status: "success",
      title: "分析",
      summary: "",
      details: {},
    }]} />);

    fireEvent.click(screen.getByRole("button", { name: /思考过程/ }));
    expect(screen.getByText("暂无可公开的分析摘要。")).toBeInTheDocument();
  });

  it("运行结束后即使最后一条思考事件仍是 running 也停止扫光动效并支持一层展开", () => {
    const staleRunning: TraceItem = {
      key: "reasoning-stale-running",
      stage: "reasoning",
      status: "running",
      title: "分析",
      summary: "",
      details: { reasoning_content: "正在检查结果" },
    };
    const { rerender } = render(<ExecutionTimeline active items={[staleRunning]} />);

    expect(screen.getByText("正在思考")).toBeVisible();
    expect(document.querySelector(".shimmer-text")).toBeInTheDocument();

    rerender(<ExecutionTimeline active={false} items={[staleRunning]} />);

    const executionTrigger = screen.getByRole("button", { name: /思考过程/ });
    expect(executionTrigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(executionTrigger);
    expect(screen.getByText("正在检查结果")).toBeVisible();
    expect(document.querySelector(".shimmer-text")).toBeNull();
  });

  it("流式来源的消息结束后仍保留执行时间线，不显示查看过程按钮", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <MessageScroller
          error={null}
          errorMessage={null}
          isRunning={false}
          loading={false}
          messages={[
            {
              id: 1,
              session_id: "session-test",
              role: "user",
              content: "帮我查资料",
              created_at: "2026-07-30T10:00:00+08:00",
              run_id: null,
            },
            {
              id: 2,
              session_id: "session-test",
              role: "assistant",
              content: "这是最终回答",
              created_at: "2026-07-30T10:00:05+08:00",
              run_id: "ask-stream",
            },
          ]}
          onCancelConfirmation={vi.fn()}
          onConfirm={vi.fn()}
          onDismissError={vi.fn()}
          onRetry={vi.fn()}
          projection={{
            ...createRunProjection({ runId: "ask-stream" }),
            status: "completed",
            responseCompleted: true,
            streamedText: "这是最终回答",
            startedAt: "2026-07-30T10:00:00+08:00",
            updatedAt: "2026-07-30T10:00:05+08:00",
            timelineItems: [{
              key: "reasoning",
              stage: "reasoning",
              status: "success",
              title: "深度思考",
              summary: "完成",
              details: { reasoning_content: "思考摘要" },
            }],
          }}
        />
      </QueryClientProvider>,
    );

    expect(screen.queryByText(/查看过程/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /思考过程/ })).toBeInTheDocument();
    return waitFor(() => {
      expect(screen.getByText("这是最终回答")).toBeInTheDocument();
    });
  });

  it("用户消息不显示悬空角色标签", () => {
    render(
      <MessageScroller
        error={null}
        errorMessage={null}
        isRunning={false}
        loading={false}
        messages={[{
          id: 1,
          session_id: "session-test",
          role: "user",
          content: "有哪些缺点？",
          created_at: "2026-07-30T10:00:00+08:00",
          run_id: null,
        }]}
        onCancelConfirmation={vi.fn()}
        onConfirm={vi.fn()}
        onDismissError={vi.fn()}
        onRetry={vi.fn()}
        projection={null}
      />,
    );

    expect(screen.queryByText("你")).not.toBeInTheDocument();
    expect(screen.getByText("有哪些缺点？")).toBeInTheDocument();
  });

  it("工具预算用中文明确区分已调用次数和上限", () => {
    render(<WorkingStatusBar projection={{
      ...createRunProjection({ runId: "ask-budget" }),
      status: "running",
      workingState: {
        run_id: "ask-budget",
        goal_summary: "查询资料",
        phase: "executing",
        current_action: "联网搜索",
        completed_steps: [],
        evidence_summaries: [],
        active_constraints: [],
        blocker: null,
        next_action: "检查搜索结果",
        budget: {
          tool_calls: 4,
          tool_call_limit: 8,
          elapsed_seconds: 2,
        },
        updated_at: "2026-07-30T10:00:02+08:00",
      },
    }} />);

    expect(screen.getByText("工具调用 4 次 · 上限 8 次")).toBeInTheDocument();
    expect(screen.queryByText("Tool 4/8")).not.toBeInTheDocument();
  });

  it("失败工具展开后只显示精简错误原因而不暴露底层路径与入参", () => {
    render(<ExecutionTimeline active={false} defaultOpen items={[{
      key: "mcp-error",
      stage: "mcp_tool_finished",
      status: "error",
      title: "读取文件",
      summary: "读取失败",
      details: {
        capability_type: "mcp",
        tool: "filesystem.read",
        inputs: { path: "/allowed/report.md" },
        error_message: "权限不足",
        output_summary: "未读取文件",
        duration_ms: 40,
      },
    }]} />);

    expect(screen.getByText("读取文件 执行失败")).toBeVisible();
    expect(screen.getByText(/权限不足/)).toBeVisible();
    expect(screen.queryByText("/allowed/report.md")).not.toBeInTheDocument();
    expect(screen.queryByText("filesystem.read")).not.toBeInTheDocument();
  });

  it("同一轮对话多次重试只保留最新回复并在再次重试时原地覆盖", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const baseMessages = [
      {
        id: 1,
        session_id: "session-retry",
        role: "user" as const,
        content: "川西近期出行适宜性",
        created_at: "2026-10-29T14:58:00+08:00",
        run_id: "ask-1",
      },
      {
        id: 2,
        session_id: "session-retry",
        role: "assistant" as const,
        content: "Model not exist.",
        created_at: "2026-10-29T14:59:00+08:00",
        run_id: "ask-1",
      },
      {
        id: 3,
        session_id: "session-retry",
        role: "assistant" as const,
        content: "Unknown model error",
        created_at: "2026-10-29T15:00:00+08:00",
        run_id: "ask-2",
      },
      {
        id: 4,
        session_id: "session-retry",
        role: "assistant" as const,
        content: "10月底川西彩林正盛，适宜出行。",
        created_at: "2026-10-29T15:05:00+08:00",
        run_id: "ask-3",
      },
    ];

    const { rerender } = render(
      <QueryClientProvider client={client}>
        <MessageScroller
          error={null}
          errorMessage={null}
          isRunning={false}
          loading={false}
          messages={baseMessages}
          onCancelConfirmation={vi.fn()}
          onConfirm={vi.fn()}
          onDismissError={vi.fn()}
          onRetry={vi.fn()}
          projection={null}
        />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("10月底川西彩林正盛，适宜出行。")).toBeInTheDocument();
    });
    expect(screen.queryByText("Model not exist.")).not.toBeInTheDocument();
    expect(screen.queryByText("Unknown model error")).not.toBeInTheDocument();

    rerender(
      <QueryClientProvider client={client}>
        <MessageScroller
          error={null}
          errorMessage={null}
          isRunning
          loading={false}
          messages={baseMessages}
          onCancelConfirmation={vi.fn()}
          onConfirm={vi.fn()}
          onDismissError={vi.fn()}
          onRetry={vi.fn()}
          projection={{
            ...createRunProjection({ runId: "ask-4" }),
            status: "responding",
            streamedText: "正在重新生成最新川西攻略…",
          }}
        />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("正在重新生成最新川西攻略…")).toBeInTheDocument();
    });
    expect(screen.queryByText("10月底川西彩林正盛，适宜出行。")).not.toBeInTheDocument();
  });
});
