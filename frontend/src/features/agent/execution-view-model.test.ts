import { describe, expect, it } from "vitest";

import type { TraceItem } from "@/lib/stream/schemas";

import {
  buildExecutionViewModels,
  mapToolState,
  toToolCallViewModel,
} from "./execution-view-model";

describe("执行过程 ViewModel", () => {
  it.each([
    ["queued", "pending"],
    ["running", "running"],
    ["completed", "completed"],
    ["success", "completed"],
    ["needs_input", "blocked"],
    ["needs_confirmation", "blocked"],
    ["blocked", "blocked"],
    ["failed", "error"],
    ["error", "error"],
  ])("将后端状态 %s 映射为 %s", (status, expected) => {
    expect(mapToolState(status)).toBe(expected);
  });

  it("分别承载能力类型、脱敏输入、结果、错误与耗时", () => {
    const tool = toToolCallViewModel(trace({
      stage: "mcp_tool_finished",
      status: "error",
      details: {
        capability_type: "mcp",
        tool: "filesystem.read",
        inputs: { path: "/allowed/report.md" },
        output_summary: "未读取文件",
        error_message: "权限不足",
        duration_ms: 1250,
      },
    }));

    expect(tool).toMatchObject({
      defaultOpen: true,
      durationMs: 1250,
      error: "权限不足",
      input: { path: "/allowed/report.md" },
      kind: "MCP",
      name: "filesystem.read",
      output: "未读取文件",
      sourceLabel: null,
      state: "error",
    });
  });

  it.each([
    [{ capability_type: "sub-agent" }, "Sub-Agent"],
    [{ skill_name: "daily-report", tool: "daily_report" }, "Tool"],
    [{ capability_type: "mcp" }, "MCP"],
    [{ tool: "weather_query" }, "Tool"],
  ])("明确区分能力类型", (details, expected) => {
    expect(toToolCallViewModel(trace({ details })).kind).toBe(expected);
  });

  it("保留轮次意图，并把思考摘要保持为可折叠内容", () => {
    const models = buildExecutionViewModels([
      trace({ key: "iteration", stage: "iteration_started", status: "running", summary: "决定调用天气工具" }),
      trace({
        key: "reasoning",
        stage: "reasoning",
        status: "running",
        details: { reasoning_content: "公开的脱敏摘要" },
      }),
    ]);

    expect(models).toEqual([
      {
        kind: "narrative",
        narrative: {
          isStreaming: true,
          key: "iteration",
          text: "决定调用天气工具",
        },
      },
      {
        kind: "reasoning",
        reasoning: {
          content: "公开的脱敏摘要",
          isStreaming: true,
          isTruncated: false,
          key: "reasoning",
          segment: 1,
          summary: "正在思考",
        },
      },
    ]);
  });

  it("同一思考过程只保留最新终态，并展示整理回复等内部步骤", () => {
    const models = buildExecutionViewModels([
      trace({ key: "prompt", stage: "prompt_ready" }),
      trace({
        key: "reasoning-running",
        stage: "reasoning",
        status: "running",
        details: { reasoning_content: "分析中" },
      }),
      trace({
        key: "reasoning-finished",
        stage: "reasoning",
        status: "success",
        details: { reasoning_content: "公开摘要" },
      }),
      trace({
        key: "final",
        stage: "final_response",
        status: "running",
        title: "整理回复",
        summary: "正在组织最终答复。",
      }),
    ]);

    expect(models).toHaveLength(2);
    expect(models[0]).toEqual({
      kind: "reasoning",
      reasoning: {
        content: "公开摘要",
        isStreaming: false,
        isTruncated: false,
        key: "reasoning-finished",
        segment: 1,
        summary: "已完成思考",
      },
    });
    expect(models[1]).toMatchObject({
      kind: "tool",
      tool: {
        key: "final",
        state: "running",
        title: "整理回复",
      },
    });
  });

  it("多段思考按段号独立展示", () => {
    const models = buildExecutionViewModels([
      trace({
        key: "reasoning-1",
        stage: "reasoning",
        status: "success",
        details: { reasoning_content: "第一次", segment: 1 },
      }),
      trace({
        key: "reasoning-2",
        stage: "reasoning",
        status: "running",
        details: { reasoning_content: "第二次", segment: 2 },
      }),
    ]);

    expect(models).toHaveLength(2);
    expect(models[0]).toMatchObject({
      kind: "reasoning",
      reasoning: { segment: 1, summary: "已完成思考" },
    });
    expect(models[1]).toMatchObject({
      kind: "reasoning",
      reasoning: { segment: 2, summary: "第 2 次思考" },
    });
  });

  it("不把 Evidence 包装、敏感参数和 URL 查询串暴露给会话界面", () => {
    const tool = toToolCallViewModel(trace({
      details: {
        tool: "web_fetch",
        skill_name: "web-fetch",
        inputs: {
          url: "https://example.com/report?id=secret#part",
          api_key: "top-secret",
        },
        output_summary: "[长工具结果已保存] evidence_id: ev-secret original_estimated_tokens: 7718 preview: {}",
      },
    }));

    expect(tool).toMatchObject({
      kind: "Tool",
      sourceLabel: "来自 Skill · web-fetch",
      input: {
        url: "https://example.com/report",
        api_key: "••••••",
      },
      output: "完整结果已安全保存，可在运行详情中查看。",
      summary: "已成功返回结果",
      title: "读取网页正文",
    });
    expect(JSON.stringify(tool)).not.toContain("ev-secret");
    expect(JSON.stringify(tool)).not.toContain("7718");
    expect(JSON.stringify(tool)).not.toContain("top-secret");
  });

  it("只有真正的 Skill 执行才标记为 Skill", () => {
    const skill = toToolCallViewModel(trace({
      stage: "skill_finished",
      details: { skill_name: "daily-report" },
    }));

    expect(skill.kind).toBe("Skill");
  });
});

function trace(overrides: Partial<TraceItem>): TraceItem {
  return {
    key: "tool",
    stage: "tool_finished",
    status: "success",
    title: "执行能力",
    summary: "执行完成",
    details: {},
    ...overrides,
  };
}
