import { describe, expect, it } from "vitest";

import { readJsonFixture, readTextFixture } from "../../test/fixture-loader";
import { runEventListResponseSchema } from "../api/schemas";
import { parseNdjsonDocument } from "./ndjson";
import {
  applyRunEvent,
  createRunProjection,
  reduceRunEvents,
  StreamRunMismatchError,
  StreamSequenceGapError,
} from "./run-projection";
import { parseStreamEvent } from "./schemas";

describe("运行状态投影", () => {
  it("把成功流投影为唯一最终回答、完成状态和合并后的 Tool 步骤", () => {
    const state = reduceRunEvents(
      parseNdjsonDocument(readTextFixture("stream-success.ndjson")),
    );

    expect(state.status).toBe("completed");
    expect(state.streamedText).toBe("成都当前 28°C。");
    expect(state.responseCompleted).toBe(true);
    expect(state.lastSequence).toBe(12);
    expect(state.timelineItems).toHaveLength(1);
    expect(state.timelineItems[0]?.stage).toBe("tool_finished");
    expect(state.workingState?.phase).toBe("completed");
    expect(state.terminalResult?.status).toBe("success");
  });

  it("区分失败、取消和等待确认终态", () => {
    const failed = reduceRunEvents(
      parseNdjsonDocument(readTextFixture("stream-failed.ndjson")),
    );
    const cancelled = reduceRunEvents(
      parseNdjsonDocument(readTextFixture("stream-cancelled.ndjson")),
    );
    const waiting = reduceRunEvents(
      parseNdjsonDocument(readTextFixture("stream-waiting-confirmation.ndjson")),
    );

    expect(failed.status).toBe("failed");
    expect(failed.error?.retriable).toBe(true);
    expect(cancelled.status).toBe("cancelled");
    expect(cancelled.terminalResult?.status).toBe("cancelled");
    expect(waiting.status).toBe("waiting_confirmation");
    expect(waiting.terminalResult?.status).toBe("needs_confirmation");
  });

  it("重复事件保持幂等，缺段和串 run 被明确拒绝", () => {
    const events = parseNdjsonDocument(readTextFixture("stream-recovery-initial.ndjson"));
    const initial = reduceRunEvents(events);

    expect(applyRunEvent(initial, events[2]!)).toBe(initial);
    expect(() => applyRunEvent(initial, parseStreamEvent({
      ...events[2],
      sequence: 5,
    }))).toThrow(StreamSequenceGapError);
    expect(() => applyRunEvent(initial, parseStreamEvent({
      ...events[2],
      run_id: "ask-other-run",
      sequence: 4,
    }))).toThrow(StreamRunMismatchError);
  });

  it("断流后从事件页游标继续投影，不重复追加 delta", () => {
    const initialEvents = parseNdjsonDocument(
      readTextFixture("stream-recovery-initial.ndjson"),
    );
    const page = runEventListResponseSchema.parse(
      readJsonFixture("stream-recovery-page.json"),
    );
    const recovered = page.events
      .map(parseStreamEvent)
      .reduce(applyRunEvent, reduceRunEvents(initialEvents));

    expect(recovered.lastSequence).toBe(6);
    expect(recovered.status).toBe("completed");
    expect(recovered.streamedText).toBe("断流前恢复后");
    expect(recovered.responseCompleted).toBe(true);
  });

  it("可以从已知游标创建恢复投影", () => {
    const state = createRunProjection({
      runId: "ask-fixture-recovery",
      lastSequence: 3,
    });
    const page = runEventListResponseSchema.parse(
      readJsonFixture("stream-recovery-page.json"),
    );
    const recovered = page.events.map(parseStreamEvent).reduce(applyRunEvent, state);

    expect(recovered.lastSequence).toBe(6);
  });

  it("response_block 在 delta 为空时追加 block.markdown", () => {
    const state = applyRunEvent(createRunProjection(), parseStreamEvent({
      version: "2.0",
      run_id: "ask-block",
      sequence: 1,
      type: "response_block",
      timestamp: "2026-07-30T10:00:00+08:00",
      payload: {
        delta: "",
        block: {
          markdown: "结构化段落",
          source_ids: ["src-1"],
        },
      },
    }));

    expect(state.streamedText).toBe("结构化段落");
  });

  it("response_block 携带 text 时以全文替换而非追加", () => {
    const initial = {
      ...createRunProjection({ runId: "ask-block-text" }),
      streamedText: "旧段落",
      lastSequence: 1,
    };
    const state = applyRunEvent(initial, parseStreamEvent({
      version: "2.0",
      run_id: "ask-block-text",
      sequence: 2,
      type: "response_block",
      timestamp: "2026-07-30T10:00:01+08:00",
      payload: {
        delta: "",
        text: "第一段\n\n第二段",
        block: {
          markdown: "第二段",
          source_ids: ["src-1"],
        },
      },
    }));

    expect(state.streamedText).toBe("第一段\n\n第二段");
  });

  it("response_completed 在文本未变化时保留已有 streamedText", () => {
    const initial = {
      ...createRunProjection({ runId: "ask-same-text" }),
      streamedText: "相同回答",
      lastSequence: 1,
    };
    const state = applyRunEvent(initial, parseStreamEvent({
      version: "2.0",
      run_id: "ask-same-text",
      sequence: 2,
      type: "response_completed",
      timestamp: "2026-07-30T10:00:01+08:00",
      payload: { text: "相同回答" },
    }));

    expect(state.streamedText).toBe("相同回答");
    expect(state.responseCompleted).toBe(true);
  });

  it("把数据库 secure_input 投影为结构化交互而不是普通缺参文本", () => {
    const event = parseStreamEvent({
      version: "2.0",
      run_id: "ask-database",
      sequence: 1,
      type: "run_waiting",
      timestamp: "2026-07-30T10:00:00+08:00",
      payload: {
        status: "needs_input",
        missing_info: [],
        interaction: {
          type: "database_connection",
          request_id: "dbi-test",
          title: "连接 MySQL",
          description: "密码不会发送给模型。",
          name: "mysql-front",
          host: "db.local",
          port: 3306,
          database: "agent_front",
          username: "agent",
          scope: "agent",
        },
      },
    });

    const state = applyRunEvent(createRunProjection(), event);

    expect(state.status).toBe("waiting_input");
    expect(state.interaction?.request_id).toBe("dbi-test");
    expect(state.interaction?.host).toBe("db.local");
  });
});
