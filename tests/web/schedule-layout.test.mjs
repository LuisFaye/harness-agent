import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../../src/daily_agent/web/index.html", import.meta.url), "utf8");
const renderSource = readFileSync(new URL("../../src/daily_agent/web/app/render.js", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("../../src/daily_agent/web/app/main.js", import.meta.url), "utf8");
const scheduleCss = readFileSync(new URL("../../src/daily_agent/web/styles/schedules.css", import.meta.url), "utf8");
const scheduleSection = page.slice(page.indexOf('id="view-schedule"'), page.indexOf('id="view-runs"'));

test("自动任务页移除日报专用表单和结果区", () => {
  assert.doesNotMatch(scheduleSection, /日报|report-form|report-output|schedule-form/);
  assert.doesNotMatch(mainSource, /\/report\/generate|\/schedules\/report/);
  assert.match(scheduleSection, /schedule-summary/);
  assert.match(scheduleSection, /schedule-list/);
});

test("自动任务使用等高卡片、状态概览和移动端单列布局", () => {
  assert.match(renderSource, /schedule-task-card/);
  assert.match(renderSource, /scheduleStatusMeta/);
  assert.match(renderSource, /已启用/);
  assert.match(renderSource, /已暂停/);
  assert.match(scheduleCss, /\.schedule-task-grid\s*\{[^}]*grid-auto-rows:\s*1fr/s);
  assert.match(scheduleCss, /\.schedule-task-card\s*\{[^}]*height:\s*100%/s);
  assert.match(scheduleCss, /@media \(max-width: 767px\).*\.schedule-task-grid\s*\{[^}]*grid-template-columns:\s*1fr/s);
});

test("自动任务操作保留立即执行、暂停恢复和运行记录", () => {
  assert.match(renderSource, /data-action="run-now"/);
  assert.match(renderSource, /data-action="\$\{item\.status === "paused" \? "resume" : "pause"\}"/);
  assert.match(renderSource, /data-action="show-runs"/);
});
