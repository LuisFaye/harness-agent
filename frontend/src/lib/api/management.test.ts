import { afterEach, describe, expect, it, vi } from "vitest";

import { updateScheduleStatus, updateToolStatus } from "./management";

describe("管理工作台 API Client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("HTTP 200 仍会拒绝自动任务业务 not_found", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({
      status: "not_found",
      job_id: 99,
    })));

    await expect(updateScheduleStatus("default", 99, "pause")).rejects.toMatchObject({
      code: "schedule_not_found",
      status: 404,
    });
  });

  it("Tool ID 使用路径编码并保留真实返回状态", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => jsonResponse({
      tool_id: "skill:weather:weather_query",
      name: "weather_query",
      description: "查询天气",
      source_type: "skill",
      source_id: "weather",
      input_schema: {},
      risk_level: "low",
      requires_confirmation: false,
      enabled: false,
      available: false,
      unavailable_reason: "对应 Tool 已单独停用。",
      last_error: null,
    }));
    vi.stubGlobal("fetch", fetchMock);

    const payload = await updateToolStatus("skill:weather:weather_query", false);

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "/tools/skill%3Aweather%3Aweather_query/disable",
    );
    expect(payload.available).toBe(false);
  });
});

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
