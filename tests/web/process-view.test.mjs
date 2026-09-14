import assert from "node:assert/strict";
import test from "node:test";

import {
  processItemsFromTrace,
  processSignature,
  renderHistoricalProcessPanel,
  renderLoadedProcessPanel,
  renderConfirmationActions,
  renderProcessPanel,
} from "../../src/daily_agent/web/app/process-view.js";

test("等待高风险操作确认时直接展示确认和取消按钮", () => {
  const html = renderConfirmationActions("needs_confirmation", "run-123");

  assert.match(html, /data-confirmation-action="confirm"/);
  assert.match(html, />确认执行<\/button>/);
  assert.match(html, /data-confirmation-action="cancel"/);
  assert.match(html, />取消<\/button>/);
  assert.match(html, /data-confirmation-run-id="run-123"/);
  assert.equal(renderConfirmationActions("success", "run-123"), "");
});

test("运行中的执行过程只展示真实步骤，不伪造执行计划", () => {
  const html = renderProcessPanel({
    items: [{
      status: "running",
      stage: "tool_started",
      text: "正在调用工具",
      meta: "搜索",
      title: "search_web",
      details: { tool: "search_web", inputs: { query: "成都天气" } },
    }],
    status: "running",
    live: true,
  });

  assert.doesNotMatch(html, /<details open class="message-process/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /execution-stream/);
  assert.match(html, /正在执行「search_web」…/);
  assert.match(html, /execution-tree/);
  assert.doesNotMatch(html, /理解需求/);
  assert.doesNotMatch(html, /制定执行计划/);
  assert.doesNotMatch(html, /process-section/);
  assert.doesNotMatch(html, /tool-call-card/);
  assert.match(html, /search_web/);
  assert.match(html, /query：成都天气/);
});

test("任务判断和执行计划摘要相同时只展示一次", () => {
  const repeatedText = "交给模型自主判断是否需要调用工具。";
  const html = renderProcessPanel({
    items: [
      { key: "route", stage: "loop_started", status: "success", text: repeatedText },
      { key: "plan", stage: "task_plan", status: "success", text: repeatedText },
      {
        key: "reasoning",
        stage: "reasoning",
        status: "success",
        text: "模型已完成分析。",
        details: { reasoning_content: "先确认用户真正想问的对象，再直接回答。" },
      },
    ],
    status: "success",
    completed: true,
  });

  assert.match(html, /判断处理方式/);
  assert.doesNotMatch(html, /制定执行计划/);
  assert.equal((html.match(/交给模型自主判断是否需要调用工具。/g) || []).length, 1);
  assert.match(html, /先确认用户真正想问的对象，再直接回答。/);
  assert.match(html, /type-reasoning">思考/);
});

test("工具调用按发生顺序完整展示，不再只保留最后十步", () => {
  const items = Array.from({ length: 12 }, (_, index) => ({
    key: `tool-${index + 1}`,
    status: index === 11 ? "running" : "success",
    stage: index === 11 ? "tool_started" : "tool_finished",
    text: `第 ${index + 1} 个工具结果`,
    meta: `tool_${index + 1}`,
    title: `tool_${index + 1}`,
    details: { tool: `tool_${index + 1}`, inputs: { index: index + 1 } },
  }));
  const html = renderProcessPanel({ items, status: "running", live: true });

  assert.ok(html.indexOf('data-step-key="tool-1"') < html.indexOf('data-step-key="tool-12"'));
  assert.match(html, /tool_1/);
  assert.match(html, /tool_12/);
  assert.match(html, /已记录 13 个步骤/);
  assert.doesNotMatch(html, /前面还有/);
});

test("有真实循环事件时按第 N 轮展示动作、结果和评估", () => {
  const html = renderProcessPanel({
    items: [
      { key: "i1s", stage: "iteration_started", status: "running", text: "决定搜索", details: { iteration: 1, tool: "search_web" } },
      { key: "i1o", stage: "iteration_observed", status: "success", text: "返回三条结果", details: { iteration: 1, tool: "search_web" } },
      { key: "i1e", stage: "iteration_evaluated", status: "success", text: "还需要价格", details: { iteration: 1, decision: "continue" } },
      { key: "i2s", stage: "iteration_started", status: "running", text: "决定查价格", details: { iteration: 2, tool: "finance" } },
    ],
    status: "running",
  });

  assert.match(html, /第 1 轮 · 调用 Tool「search_web」/);
  assert.match(html, /结果验证：还需要价格/);
  assert.match(html, /第 2 轮 · 调用 Tool「finance」/);
  assert.doesNotMatch(html, /理解需求/);
});

test("执行流签名只在步骤或状态变化时改变", () => {
  const items = [{ key: "tool-1", status: "running", stage: "tool_started", text: "正在搜索" }];
  const first = processSignature(items, "running");
  const same = processSignature([{ ...items[0] }], "running");
  const finished = processSignature([{ ...items[0], status: "success" }], "running");

  assert.equal(first, same);
  assert.notEqual(first, finished);
});

test("成功完成后自动折叠，失败时保持展开", () => {
  const success = renderProcessPanel({ items: [], status: "success", completed: true });
  const failure = renderProcessPanel({ items: [], status: "error", completed: true });

  assert.doesNotMatch(success, /<details open/);
  assert.match(success, /已完成/);
  assert.match(failure, /<details open/);
  assert.match(failure, /执行失败/);
});

test("历史消息先显示折叠入口，展开后只渲染脱敏 Trace 摘要", () => {
  const shell = renderHistoricalProcessPanel("ask-001");
  const detail = renderLoadedProcessPanel({
    run_id: "ask-001",
    status: "success",
    duration_ms: 1200,
    trace: [{
      key: "tool-1",
      stage: "tool_call",
      status: "success",
      title: "调用搜索",
      summary: "已返回 3 条结果 <safe>",
      details: {
        tool: "search_web",
        inputs: { query: "特斯拉财报", api_key: "不能进入聊天过程" },
        output_summary: "已返回 3 条结果 <safe>",
        secret: "不能进入聊天过程",
      },
    }],
  });
  const items = processItemsFromTrace([{ stage: "memory", summary: "读取相关记忆" }]);

  assert.match(shell, /data-process-loaded="false"/);
  assert.match(shell, /展开后加载/);
  assert.match(detail, /已返回 3 条结果 &lt;safe&gt;/);
  assert.doesNotMatch(detail, /不能进入聊天过程/);
  assert.match(detail, /api_key：\*\*\*/);
  assert.equal(items[0].meta, "记忆");
  assert.deepEqual(items[0].details, {});
});

test("Skill 是父节点，内部 Tool 使用缩进树线而不是嵌套卡片", () => {
  const html = renderProcessPanel({
    items: [
      {
        key: "tool-1",
        stage: "tool_finished",
        status: "success",
        title: "read_file",
        details: { tool: "read_file", skill_name: "数据分析", duration_ms: 320, attempts: 1 },
      },
      {
        key: "tool-2",
        stage: "tool_finished",
        status: "success",
        title: "query_database",
        details: { tool: "query_database", skill_name: "数据分析", duration_ms: 680, attempts: 2 },
      },
    ],
    status: "success",
    completed: true,
    durationMs: 8000,
  });

  assert.match(html, /执行「数据分析」/);
  assert.match(html, /type-skill">Skill/);
  assert.match(html, /execution-children/);
  assert.match(html, /调用 Tool「read_file」/);
  assert.match(html, /调用 Tool「query_database」/);
  assert.match(html, /重试次数/);
  assert.match(html, /1 次/);
  assert.match(html, /处理了 8 秒 · 执行了 4 个步骤/);
  assert.doesNotMatch(html, /tool-call-card/);
});

test("MCP 显示为 Tool 的接入来源，Sub-Agent 使用独立重要卡片", () => {
  const html = renderProcessPanel({
    items: [
      {
        key: "tool-mcp",
        stage: "tool_finished",
        status: "success",
        title: "mcp_database_query",
        details: { tool: "mcp_database_query", duration_ms: 90 },
      },
      {
        key: "sub-1",
        stage: "sub_agent_started",
        status: "running",
        title: "图表分析",
        details: { agent_name: "图表分析" },
      },
    ],
    status: "running",
  });

  assert.match(html, /调用 Tool「mcp_database_query」/);
  assert.match(html, /type-mcp">MCP · database/);
  assert.match(html, /由 database MCP 接入/);
  assert.match(html, /Sub-Agent「图表分析」执行独立子任务/);
  assert.match(html, /important-execution-card/);
});

test("同一次 Tool 的开始和完成事件合并为一个稳定步骤", () => {
  const html = renderProcessPanel({
    items: [
      { key: "tool-1-started", stage: "tool_started", status: "running", title: "search", details: { tool: "search", iteration: 1 } },
      { key: "tool-1-finished", stage: "tool_finished", status: "success", title: "search", details: { tool: "search", iteration: 1, duration_ms: 100 } },
    ],
    status: "success",
    completed: true,
  });

  assert.equal((html.match(/调用 Tool「search」/g) || []).length, 1);
  assert.match(html, /100 毫秒/);
});

test("失败并需要重试时允许使用异常卡片，并显示错误和重试次数", () => {
  const html = renderProcessPanel({
    items: [{
      key: "tool-error",
      stage: "tool_finished",
      status: "error",
      title: "query_database",
      text: "数据库暂时不可用",
      details: {
        tool: "query_database",
        error_message: "连接超时",
        attempts: 3,
      },
    }],
    status: "error",
    completed: true,
  });

  assert.match(html, /important-execution-card/);
  assert.match(html, /连接超时/);
  assert.match(html, /重试次数/);
  assert.match(html, /2 次/);
});
