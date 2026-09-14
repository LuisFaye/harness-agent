import assert from "node:assert/strict";
import test from "node:test";

globalThis.document = {
  getElementById: () => null,
  querySelectorAll: () => [],
};

const { activityFromTraceItem } = await import("../../src/daily_agent/web/app/trace.js");

test("工具轮次结果不会把原始 JSON 写进输入区状态", () => {
  const rawResult = '{"pages":[{"snippet":"一段很长的搜索结果"}]}';
  const activity = activityFromTraceItem({
    key: "iteration-1-observed",
    stage: "iteration_observed",
    status: "success",
    summary: rawResult,
    details: { iteration: 1, result: rawResult },
  });

  assert.equal(activity.text, "第 1 轮工具结果已返回，正在判断是否继续...");
  assert.doesNotMatch(activity.text, /pages|snippet/);
});

test("未知事件也不会把任意摘要写进输入区状态", () => {
  const activity = activityFromTraceItem({
    key: "future-event",
    stage: "future_event",
    status: "running",
    summary: '{"secret":"raw tool output"}',
    details: {},
  });

  assert.equal(activity.text, "正在处理当前步骤...");
  assert.doesNotMatch(activity.text, /secret|raw tool output/);
});
