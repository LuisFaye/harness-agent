import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const indexHtml = readFileSync(new URL("../../src/daily_agent/web/index.html", import.meta.url), "utf8");
const renderSource = readFileSync(new URL("../../src/daily_agent/web/app/render.js", import.meta.url), "utf8");
const traceSource = readFileSync(new URL("../../src/daily_agent/web/app/trace.js", import.meta.url), "utf8");
const streamSource = readFileSync(new URL("../../src/daily_agent/web/app/stream-render.js", import.meta.url), "utf8");
const activeProgressCss = readFileSync(new URL("../../src/daily_agent/web/styles/active-run-progress.css", import.meta.url), "utf8");
const scrollSource = readFileSync(new URL("../../src/daily_agent/web/app/chat-scroll.js", import.meta.url), "utf8");
const tokensCss = readFileSync(new URL("../../src/daily_agent/web/styles/tokens.css", import.meta.url), "utf8");
const v4Css = readFileSync(new URL("../../src/daily_agent/web/styles/v4.css", import.meta.url), "utf8");
const responsiveCss = readFileSync(new URL("../../src/daily_agent/web/styles/v4-responsive.css", import.meta.url), "utf8");

test("V4 会话页使用紧凑顶栏、会话内实时过程和按需运行抽屉", () => {
  assert.match(indexHtml, /class="topbar conversation-topbar"/);
  assert.doesNotMatch(indexHtml, /class="activity-bar"/);
  assert.doesNotMatch(indexHtml, /id="view-active-run-details"/);
  assert.match(indexHtml, /class="run-drawer"/);
  assert.match(indexHtml, /id="run-drawer-process"/);
  assert.doesNotMatch(indexHtml, /conversation-mode-badge/);
  assert.doesNotMatch(indexHtml, /<details class="working-status"/);
});

test("普通消息不再渲染卡片或内嵌完整执行树", () => {
  assert.match(renderSource, /class="message-content"/);
  assert.match(renderSource, /class="message-run-summary"/);
  assert.doesNotMatch(renderSource, /class="message-card"/);
  assert.doesNotMatch(renderSource, /renderHistoricalProcessPanel/);
  assert.match(traceSource, /class="message-row assistant active-run/);
  assert.match(traceSource, /renderActiveRunProgress\(run\)/);
  assert.doesNotMatch(traceSource, /renderProcessPanel\(\{/);
  assert.match(streamSource, /card\.classList\.toggle\("has-response", Boolean\(run\.streamedText\)\)/);
  assert.doesNotMatch(v4Css, /\.active-run:not\(\.has-response\)/);
  assert.match(activeProgressCss, /\.active-thinking-logo::after/);
  assert.match(activeProgressCss, /@media \(prefers-reduced-motion:\s*reduce\)/);
});

test("设计令牌和响应式断点符合 V4 布局契约", () => {
  assert.match(tokensCss, /--bg:\s*#f7f7f5/i);
  assert.match(tokensCss, /--primary:\s*#0f766e/i);
  assert.match(tokensCss, /--sidebar-width:\s*72px/);
  assert.match(tokensCss, /--session-width:\s*264px/);
  assert.match(tokensCss, /--content-max:\s*860px/);
  assert.match(v4Css, /grid-template-columns:\s*var\(--sidebar-width\)\s+minmax\(0,\s*1fr\)/);
  assert.match(v4Css, /\.sidebar\s*\{[^}]*scrollbar-gutter:\s*auto/s);
  assert.match(v4Css, /\.main-nav\s*\{[^}]*align-self:\s*stretch[^}]*width:\s*auto/s);
  assert.match(v4Css, /\.nav-item\s*\{[^}]*white-space:\s*nowrap/s);
  assert.match(v4Css, /\.starter-panel\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)[^}]*justify-content:\s*center/s);
  assert.match(v4Css, /\.agent-session-panel \.session-list::-webkit-scrollbar\s*\{[^}]*display:\s*none/s);
  assert.match(responsiveCss, /@media \(max-width:\s*1099px\)/);
  assert.match(responsiveCss, /@media \(min-width:\s*768px\) and \(max-width:\s*820px\)/);
  assert.match(responsiveCss, /@media \(max-width:\s*767px\)/);
  assert.match(responsiveCss, /@media \(prefers-reduced-motion:\s*reduce\)/);
});

test("Composer 保留安全区且流式滚动只在接近底部时跟随", () => {
  assert.match(v4Css, /env\(safe-area-inset-bottom\)/);
  assert.match(v4Css, /\.composer textarea\s*\{[^}]*max-height:\s*168px/s);
  assert.match(scrollSource, /FOLLOW_THRESHOLD\s*=\s*80/);
  assert.match(scrollSource, /state\.chatStickToBottom/);
  assert.match(scrollSource, /elements\.jumpToLatest\.hidden/);
  assert.match(traceSource, /followChatIfNeeded\(\)/);
});
