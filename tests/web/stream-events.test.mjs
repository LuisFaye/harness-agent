import assert from "node:assert/strict";
import test from "node:test";

import { normalizeStreamEvent } from "../../src/daily_agent/web/app/stream-events.js";

function event(sequence, type = "response_delta", payload = { delta: "A" }) {
  return {
    version: "2.0",
    run_id: "ask-001",
    sequence,
    type,
    timestamp: "2026-07-15T18:00:00+08:00",
    payload,
  };
}

test("v2 流式事件按 sequence 推进游标", () => {
  const first = normalizeStreamEvent(event(1, "run_started", { status: "running" }));
  const second = normalizeStreamEvent(event(2), first.cursor);

  assert.equal(first.event.runId, "ask-001");
  assert.deepEqual(second.cursor, { runId: "ask-001", lastSequence: 2 });
  assert.equal(second.event.payload.delta, "A");
});

test("重复事件被忽略，不会重复追加 delta", () => {
  const duplicate = normalizeStreamEvent(event(2), { runId: "ask-001", lastSequence: 2 });

  assert.equal(duplicate.ignored, true);
});

test("缺段、串 run 和非法 payload 会被拒绝", () => {
  assert.throws(
    () => normalizeStreamEvent(event(3), { runId: "ask-001", lastSequence: 1 }),
    /缺少第 2 段/,
  );
  assert.throws(
    () => normalizeStreamEvent({ ...event(2), run_id: "ask-002" }, { runId: "ask-001", lastSequence: 1 }),
    /其他运行/,
  );
  assert.throws(
    () => normalizeStreamEvent({ ...event(1), payload: [] }),
    /格式不正确/,
  );
});

test("取消受理和取消完成事件属于合法 v2 事件", () => {
  const requested = normalizeStreamEvent(
    event(1, "run_cancel_requested", { status: "cancel_requested" }),
  );
  const cancelled = normalizeStreamEvent(
    event(2, "run_cancelled", { status: "cancelled", result: { status: "cancelled" } }),
    requested.cursor,
  );

  assert.equal(requested.event.type, "run_cancel_requested");
  assert.equal(cancelled.event.type, "run_cancelled");
  assert.equal(cancelled.cursor.lastSequence, 2);
});
