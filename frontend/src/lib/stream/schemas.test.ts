import { describe, expect, it } from "vitest";

import { readTextFixture } from "../../test/fixture-loader";
import { parseNdjsonDocument } from "./ndjson";
import { parseStreamEvent, STREAM_EVENT_TYPES } from "./schemas";

const streamFixtures = [
  "stream-success.ndjson",
  "stream-failed.ndjson",
  "stream-cancelled.ndjson",
  "stream-waiting-confirmation.ndjson",
  "stream-recovery-initial.ndjson",
];

describe("NDJSON v2 契约", () => {
  it.each(streamFixtures)("%s 中每条事件都通过公开契约校验", (fixtureName) => {
    const events = parseNdjsonDocument(readTextFixture(fixtureName));

    expect(events.length).toBeGreaterThan(0);
    expect(events.map((event) => event.sequence)).toEqual(
      Array.from({ length: events.length }, (_, index) => index + 1),
    );
    expect(new Set(events.map((event) => event.run_id)).size).toBe(1);
  });

  it("前端事件类型与当前后端公开类型数量一致", () => {
    expect(STREAM_EVENT_TYPES).toHaveLength(13);
  });

  it("拒绝错误协议版本、未知类型和不完整关键 payload", () => {
    const baseEvent = {
      version: "2.0",
      run_id: "ask-invalid",
      sequence: 1,
      type: "response_delta",
      timestamp: "2026-07-28T10:00:00+08:00",
      payload: { delta: "A" },
    };

    expect(() => parseStreamEvent({ ...baseEvent, version: "1.0" })).toThrow();
    expect(() => parseStreamEvent({ ...baseEvent, type: "unknown" })).toThrow();
    expect(() => parseStreamEvent({ ...baseEvent, payload: {} })).toThrow();
  });
});
