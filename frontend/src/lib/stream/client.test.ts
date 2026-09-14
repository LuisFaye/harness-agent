import { describe, expect, it, vi } from "vitest";

import { runEventListResponseSchema } from "@/lib/api/schemas";
import { readJsonFixture, readTextFixture } from "@/test/fixture-loader";

import { parseNdjsonDocument } from "./ndjson";
import { createRunProjection, reduceRunEvents } from "./run-projection";
import { recoverAgentRun, streamAgentRun } from "./client";

const request = {
  query: "查询成都天气",
  user_id: "default",
  session_id: "session-test",
  client_messages: [],
  attachment_ids: [],
};

describe("Agent 流客户端", () => {
  it("消费分块 NDJSON 并持续发布唯一运行投影", async () => {
    const content = readTextFixture("stream-success.ndjson");
    const bytes = new TextEncoder().encode(content);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 137));
        controller.enqueue(bytes.slice(137, 811));
        controller.enqueue(bytes.slice(811));
        controller.close();
      },
    });
    const projections: number[] = [];
    const finalProjection = await streamAgentRun(request, {
      fetchImpl: vi.fn(async () => new Response(body, {
        headers: { "X-Run-Id": "ask-fixture-success" },
      })),
      onProjection: (projection) => projections.push(projection.lastSequence),
    });

    expect(finalProjection.status).toBe("completed");
    expect(finalProjection.streamedText).toBe("成都当前 28°C。");
    expect(finalProjection.timelineItems).toHaveLength(1);
    expect(projections.at(-1)).toBe(12);
  });

  it("从断流游标补拉持久化事件直到终态", async () => {
    const initial = reduceRunEvents(
      parseNdjsonDocument(readTextFixture("stream-recovery-initial.ndjson")),
    );
    const page = runEventListResponseSchema.parse(
      readJsonFixture("stream-recovery-page.json"),
    );
    const finalProjection = await recoverAgentRun(
      "default",
      "ask-fixture-recovery",
      initial,
      {
        fetchEventPage: vi.fn(async () => page),
        wait: vi.fn(async () => undefined),
        maxRecoveryAttempts: 1,
      },
    );

    expect(finalProjection.status).toBe("completed");
    expect(finalProjection.streamedText).toBe("断流前恢复后");
    expect(finalProjection.lastSequence).toBe(6);
  });

  it("恢复超过限制后提供可操作的错误信息", async () => {
    await expect(recoverAgentRun(
      "default",
      "ask-still-running",
      createRunProjection({ runId: "ask-still-running" }),
      {
        fetchEventPage: vi.fn(async () => ({
          run_id: "ask-still-running",
          events: [],
          last_sequence: 0,
          has_more: false,
        })),
        wait: vi.fn(async () => undefined),
        maxRecoveryAttempts: 2,
      },
    )).rejects.toThrow("请到运行监控查看后台状态");
  });
});
