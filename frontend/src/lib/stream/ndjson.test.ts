import { describe, expect, it } from "vitest";

import { readTextFixture } from "../../test/fixture-loader";
import { NdjsonByteDecoder, NdjsonLineDecoder } from "./ndjson";

describe("NDJSON 分块解析", () => {
  it("可以跨任意字符串分块保留尾包", () => {
    const content = readTextFixture("stream-cancelled.ndjson");
    const decoder = new NdjsonLineDecoder();
    const events = [
      ...decoder.push(content.slice(0, 17)),
      ...decoder.push(content.slice(17, 91)),
      ...decoder.push(content.slice(91)),
      ...decoder.finish(),
    ];

    expect(events.map((event) => event.type)).toEqual([
      "run_started",
      "run_cancel_requested",
      "run_cancelled",
    ]);
  });

  it("中文字符被拆成多个字节时仍可正确恢复", () => {
    const bytes = new TextEncoder().encode(readTextFixture("stream-failed.ndjson"));
    const decoder = new NdjsonByteDecoder();
    const events = [];

    for (let index = 0; index < bytes.length; index += 7) {
      events.push(...decoder.push(bytes.slice(index, index + 7)));
    }
    events.push(...decoder.finish());

    expect(events.at(-1)?.payload.message).toBe("模拟远程服务不可用");
  });

  it("末行没有换行符时由 finish 完成解析", () => {
    const content = readTextFixture("stream-recovery-initial.ndjson").trimEnd();
    const decoder = new NdjsonLineDecoder();

    const events = [...decoder.push(content), ...decoder.finish()];

    expect(events).toHaveLength(3);
    expect(events.at(-1)?.sequence).toBe(3);
  });
});
