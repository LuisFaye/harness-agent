import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const layoutCss = readFileSync(new URL("../../src/daily_agent/web/styles/layout.css", import.meta.url), "utf8");
const capabilitiesCss = readFileSync(new URL("../../src/daily_agent/web/styles/capabilities.css", import.meta.url), "utf8");
const capabilityRender = readFileSync(new URL("../../src/daily_agent/web/app/capability-render.js", import.meta.url), "utf8");
const processView = readFileSync(new URL("../../src/daily_agent/web/app/process-view.js", import.meta.url), "utf8");
const traceView = readFileSync(new URL("../../src/daily_agent/web/app/trace.js", import.meta.url), "utf8");
const page = readFileSync(new URL("../../src/daily_agent/web/index.html", import.meta.url), "utf8");

test("能力中心和 MCP 长表单都保留垂直滚动能力", () => {
  assert.match(layoutCss, /#view-capabilities\s*\{[^}]*overflow:\s*auto/s);
  assert.match(capabilitiesCss, /\.capability-mcp-form\s*\{[^}]*max-height:[^;}]+;[^}]*overflow-y:\s*auto/s);
  assert.match(capabilitiesCss, /@media \(max-width: 800px\)[^{]*\{.*\.capability-mcp-form\s*\{[^}]*max-height:\s*none;[^}]*overflow:\s*visible/s);
});

test("能力卡片使用统一网格和固定信息层级", () => {
  assert.match(capabilitiesCss, /\.capability-grid\s*\{[^}]*grid-auto-rows:\s*1fr/s);
  assert.match(capabilitiesCss, /\.capability-card\s*\{[^}]*display:\s*flex;[^}]*height:\s*100%/s);
  assert.match(capabilitiesCss, /\.capability-actions\s*\{[^}]*margin-top:\s*auto/s);
  assert.match(capabilityRender, /capability-card-title/);
  assert.match(capabilityRender, /capability-description/);
  assert.match(capabilityRender, /capability-reason/);
});

test("能力状态同时使用符号、文字和高对比色", () => {
  assert.match(capabilityRender, /capability-status-icon/);
  assert.match(capabilityRender, /可调用/);
  assert.match(capabilityRender, /连接失败/);
  assert.match(capabilitiesCss, /\.status-ready\s*\{[^}]*color:\s*#11643b/s);
  assert.match(capabilitiesCss, /\.status-error\s*\{[^}]*color:\s*#9b2525/s);
});

test("思考过程不暴露具体模型名称", () => {
  const reasoningUi = [processView, traceView, page].join("\n");
  assert.doesNotMatch(reasoningUi, /DeepSeek[^<\n]*(?:深度)?思考/);
  assert.match(processView, /Agent 正在思考/);
  assert.match(traceView, /Agent 已完成思考/);
});
