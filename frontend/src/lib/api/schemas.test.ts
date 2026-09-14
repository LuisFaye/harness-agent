import { describe, expect, it } from "vitest";

import { readJsonFixture } from "../../test/fixture-loader";
import {
  askRequestSchema,
  runCancelResponseSchema,
  runEventListResponseSchema,
  runListResponseSchema,
  sessionDetailResponseSchema,
  sessionListResponseSchema,
} from "./schemas";

describe("核心 REST 契约", () => {
  it("校验聊天请求、会话列表和会话详情", () => {
    expect(askRequestSchema.parse({
      query: "检查项目",
      user_id: "default",
      session_id: "session-1",
      client_messages: [{ role: "user", content: "上一条" }],
      attachment_ids: [],
    }).query).toBe("检查项目");

    expect(sessionListResponseSchema.parse({
      items: [{
        session_id: "session-1",
        title: "项目检查",
        preview: "上一条消息",
        message_count: 2,
        last_message_at: "2026-07-28T10:00:00+08:00",
        has_summary: false,
      }],
      total: 1,
      limit: 30,
      offset: 0,
    }).items).toHaveLength(1);

    expect(sessionDetailResponseSchema.parse({
      session_id: "session-1",
      messages: [{
        id: 1,
        session_id: "session-1",
        role: "user",
        content: "检查项目",
        created_at: null,
        run_id: null,
      }],
      summary: null,
      context_observation: null,
    }).messages[0]?.role).toBe("user");
  });

  it("校验运行列表、事件恢复页和取消响应", () => {
    const run = {
      run_id: "ask-1",
      user_id: "default",
      session_id: "session-1",
      status: "success",
      query_summary: "检查项目",
      response_summary: "检查完成",
      execution_source: "agent_loop",
      model_id: "fixture-model",
      error_stage: null,
      started_at: "2026-07-28T10:00:00+08:00",
      finished_at: "2026-07-28T10:00:01+08:00",
      duration_ms: 1000,
      total_tokens: null,
      has_detail: true,
      legacy_record: false,
    };

    expect(runListResponseSchema.parse({
      items: [run],
      total: 1,
      limit: 20,
      offset: 0,
    }).items[0]?.run_id).toBe("ask-1");
    expect(runEventListResponseSchema.parse(
      readJsonFixture("stream-recovery-page.json"),
    ).last_sequence).toBe(6);
    expect(runCancelResponseSchema.parse({
      run_id: "ask-1",
      status: "cancel_requested",
      accepted: true,
      executor_notified: true,
      message: "停止请求已提交。",
    }).accepted).toBe(true);
  });
});
