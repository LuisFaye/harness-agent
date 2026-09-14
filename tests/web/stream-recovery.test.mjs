import assert from "node:assert/strict";
import test from "node:test";

import { recoverPersistedEvents } from "../../src/daily_agent/web/app/stream-recovery.js";

test("断流后按 lastSequence 分页补拉直到终态", async () => {
  const calls = [];
  const cursor = { runId: "ask-001", lastSequence: 2 };
  const pages = [
    {
      events: [{ sequence: 3, type: "response_delta" }],
      has_more: true,
    },
    {
      events: [{ sequence: 4, type: "run_completed", payload: { result: { status: "success" } } }],
      has_more: false,
    },
  ];

  const result = await recoverPersistedEvents({
    runId: "ask-001",
    userId: "u1",
    cursor,
    fetchPage: async (params) => {
      calls.push(params);
      return pages.shift();
    },
    consumeEvent: (event, current, currentCursor) => {
      currentCursor.lastSequence = event.sequence;
      return event.type === "run_completed" ? event.payload.result : current;
    },
    wait: async () => {},
  });

  assert.deepEqual(result, { status: "success" });
  assert.deepEqual(calls.map((item) => item.afterSequence), [2, 3]);
  assert.equal(cursor.lastSequence, 4);
});

test("超过恢复次数后给出明确错误", async () => {
  await assert.rejects(
    recoverPersistedEvents({
      runId: "ask-001",
      userId: "u1",
      cursor: { runId: "ask-001", lastSequence: 0 },
      fetchPage: async () => ({ events: [], has_more: false }),
      consumeEvent: () => null,
      wait: async () => {},
      maxAttempts: 2,
    }),
    /运行监控/,
  );
});
