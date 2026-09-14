// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildRunsPath } from "@/lib/api/agent";
import type { RunDetail } from "@/lib/api/schemas";

import { RunDetailPanel } from "./run-detail-panel";
import {
  formatDuration,
  historicalRunProjection,
  parseRunTrace,
  runStatusLabel,
} from "./run-format";

afterEach(cleanup);

describe("运行监控", () => {
  it("只提交有效筛选并正确编码特殊字符", () => {
    expect(buildRunsPath("default", {
      status: "error",
      sessionId: "会话/一",
      keyword: "天气 & run",
      limit: 20,
      offset: 40,
    })).toBe(
      "/runs?user_id=default&limit=20&offset=40&status=error&session_id=%E4%BC%9A%E8%AF%9D%2F%E4%B8%80&keyword=%E5%A4%A9%E6%B0%94+%26+run",
    );
    expect(buildRunsPath("default", {
      status: " ",
      sessionId: "",
      keyword: " ",
    })).toBe("/runs?user_id=default&limit=20&offset=0");
  });

  it("将持久化状态和 Trace 转换为共享运行投影", () => {
    const projection = historicalRunProjection(runDetail);
    expect(projection.status).toBe("failed");
    expect(projection.workingState?.goal_summary).toBe("排查接口失败");
    expect(parseRunTrace(runDetail.trace)).toHaveLength(1);
    expect(runStatusLabel("needs_confirmation")).toBe("等待确认");
    expect(formatDuration(1450)).toBe("1.4 秒");
  });

  it("展示安全详情、Evidence 和错误，不展示完整 reasoning_content", async () => {
    render(
      <MemoryRouter>
        <RunDetailPanel
          cancelError={null}
          cancelPending={false}
          detail={runDetail}
          error={null}
          fetching={false}
          loading={false}
          onBack={vi.fn()}
          onCancel={vi.fn()}
          onRefresh={vi.fn()}
          runId={runDetail.run_id}
        />
      </MemoryRouter>,
    );

    expect(screen.getAllByText("排查接口失败")).toHaveLength(2);
    expect(screen.getByText("接口返回 503。")).toBeInTheDocument();
    expect(screen.getByText("web_fetch")).toBeInTheDocument();
    expect(screen.getByText("网页请求失败")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("上游服务不可用");
    expect(screen.queryByText("不可展示的完整内部推理")).not.toBeInTheDocument();
    expect(await screen.findByText("请稍后重试。", {}, { timeout: 3000 })).toBeInTheDocument();
  });
});

const runDetail: RunDetail = {
  run_id: "ask-run-error",
  user_id: "default",
  session_id: "session-test",
  status: "error",
  query_summary: "排查接口失败",
  response_summary: "请稍后重试。",
  execution_source: "agent_loop",
  model_id: "test-model",
  error_stage: "tool_execution",
  started_at: "2026-07-28T10:00:00+08:00",
  finished_at: "2026-07-28T10:00:01+08:00",
  duration_ms: 1450,
  total_tokens: 20,
  has_detail: true,
  legacy_record: false,
  query: "排查接口失败",
  response_text: "请稍后重试。",
  stop_reason: "工具执行失败",
  missing_info: [],
  plan: {
    steps: [{ name: "读取接口", tool: "web_fetch", reason: "获取真实响应" }],
  },
  observations: [{
    name: "网页读取",
    tool: "web_fetch",
    status: "error",
    duration_ms: 120,
    error_message: "网页请求失败",
  }],
  trace: [{
    key: "reasoning",
    stage: "reasoning",
    status: "success",
    title: "深度思考",
    summary: "模型已完成分析。",
    details: { reasoning_content: "不可展示的完整内部推理" },
  }],
  prompt_snapshot: null,
  model_requests: [],
  working_state: {
    run_id: "ask-run-error",
    goal_summary: "排查接口失败",
    phase: "failed",
    current_action: "任务执行失败",
    completed_steps: [],
    evidence_summaries: [{
      tool: "web_fetch",
      status: "error",
      summary: "接口返回 503。",
      evidence_id: "ev-test",
    }],
    active_constraints: [],
    blocker: "上游服务不可用",
    next_action: "稍后重试",
    budget: { tool_calls: 1, tool_call_limit: 8, elapsed_seconds: 1 },
    updated_at: "2026-07-28T10:00:01+08:00",
  },
  usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 },
  error_message: "上游服务不可用",
  truncated_fields: [],
};
