import assert from "node:assert/strict";
import test from "node:test";

import {
  buildWorkspaceLocation,
  buildRunsQuery,
  readRunLocation,
} from "../../src/daily_agent/web/app/run-state.js";

test("buildRunsQuery 只提交有效筛选并正确编码特殊字符", () => {
  const url = buildRunsQuery({
    userId: "u 1",
    status: "success",
    sessionId: "work/main",
    keyword: "%_ 项目",
    limit: 10,
    offset: 20,
  });
  const parsed = new URL(url, "http://localhost");

  assert.equal(parsed.pathname, "/runs");
  assert.equal(parsed.searchParams.get("user_id"), "u 1");
  assert.equal(parsed.searchParams.get("status"), "success");
  assert.equal(parsed.searchParams.get("session_id"), "work/main");
  assert.equal(parsed.searchParams.get("keyword"), "%_ 项目");
  assert.equal(parsed.searchParams.get("limit"), "10");
  assert.equal(parsed.searchParams.get("offset"), "20");
});

test("buildRunsQuery 不提交空筛选", () => {
  const parsed = new URL(buildRunsQuery({ userId: "default" }), "http://localhost");

  assert.equal(parsed.searchParams.has("status"), false);
  assert.equal(parsed.searchParams.has("session_id"), false);
  assert.equal(parsed.searchParams.has("keyword"), false);
});

test("readRunLocation 可以恢复运行监控深链", () => {
  assert.deepEqual(readRunLocation("?view=runs&run_id=ask-123"), {
    view: "runs",
    runId: "ask-123",
    sessionId: "",
  });
  assert.deepEqual(readRunLocation("?session_id=session-123"), {
    view: "",
    runId: "",
    sessionId: "session-123",
  });
  assert.deepEqual(readRunLocation(""), { view: "", runId: "", sessionId: "" });
});

test("聊天会话写入 URL，刷新后可以恢复同一个 session", () => {
  const url = buildWorkspaceLocation("http://localhost/?view=runs&run_id=ask-old", {
    viewName: "chat",
    sessionId: "session-new",
  });

  assert.equal(url.searchParams.get("session_id"), "session-new");
  assert.equal(url.searchParams.has("view"), false);
  assert.equal(url.searchParams.has("run_id"), false);
});

test("进入运行监控时清理聊天 session 深链", () => {
  const url = buildWorkspaceLocation("http://localhost/?session_id=session-new", {
    viewName: "runs",
    runId: "ask-123",
  });

  assert.equal(url.searchParams.get("view"), "runs");
  assert.equal(url.searchParams.get("run_id"), "ask-123");
  assert.equal(url.searchParams.has("session_id"), false);
});
