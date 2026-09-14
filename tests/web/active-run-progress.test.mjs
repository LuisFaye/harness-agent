import assert from "node:assert/strict";
import test from "node:test";

import { renderActiveRunProgress } from "../../src/daily_agent/web/app/active-run-progress.js";

test("运行开始后立即在会话内展示思考动效和真实步骤", () => {
  const html = renderActiveRunProgress({
    status: "running",
    activityItems: [
      {
        key: "route",
        stage: "loop_started",
        status: "success",
        text: "交给模型自主判断是否需要工具",
        details: { route: "agent_loop" },
      },
      {
        key: "tool",
        stage: "tool_started",
        status: "running",
        text: "正在调用 web_search",
        details: { tool: "web_search", api_key: "secret-value" },
      },
    ],
  });

  assert.match(html, /active-thinking-logo/);
  assert.match(html, /正在思考\.\.\./);
  assert.match(html, /Agent 实时执行过程/);
  assert.match(html, /判断处理方式/);
  assert.match(html, /web_search/);
  assert.match(html, />Tool</);
  assert.match(html, /data-active-run-action="details"/);
  assert.match(html, /data-active-run-action="stop"/);
  assert.doesNotMatch(html, /secret-value/);
});

test("Skill、MCP 和 Sub-Agent 步骤按能力类型展示", () => {
  const html = renderActiveRunProgress({
    status: "running",
    activityItems: [
      {
        key: "skill-tool",
        stage: "tool_finished",
        status: "success",
        text: "日报已生成",
        details: { tool: "daily_report", skill_name: "daily-report" },
      },
      {
        key: "mcp-tool",
        stage: "tool_started",
        status: "running",
        text: "正在搜索",
        details: { tool: "search", mcp_server_name: "search-server" },
      },
      {
        key: "subagent",
        stage: "sub_agent_started",
        status: "running",
        text: "正在处理子任务",
        details: { agent_name: "researcher" },
      },
    ],
  });

  assert.match(html, />Skill</);
  assert.match(html, />MCP</);
  assert.match(html, />Sub-Agent</);
  assert.match(html, /is-child/);
});
