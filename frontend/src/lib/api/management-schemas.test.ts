import { describe, expect, it } from "vitest";

import {
  mcpServerListSchema,
  modelConfigResponseSchema,
  modelConnectionResponseSchema,
  scheduleListResponseSchema,
  skillListResponseSchema,
  toolListResponseSchema,
} from "./management-schemas";

describe("管理工作台 REST 契约", () => {
  it("校验自动任务列表", () => {
    const payload = scheduleListResponseSchema.parse({
      items: [{
        id: 3,
        user_id: "default",
        job_type: "daily_report",
        title: "工作日报",
        status: "enabled",
        schedule_kind: "daily",
        schedule_time: "18:00",
        timezone: "Asia/Shanghai",
        config_json: "{\"repos\":[\"/workspace\"]}",
        next_run_at: "2026-07-29T18:00:00+08:00",
        last_run_at: null,
        created_at: null,
        updated_at: null,
      }],
    });
    expect(payload.items[0]?.status).toBe("enabled");
  });

  it("校验 Tool 与 Skill 的真实可用状态", () => {
    const toolPayload = toolListResponseSchema.parse({
      total: 1,
      items: [{
        tool_id: "skill:weather:weather_query",
        name: "weather_query",
        description: "查询天气",
        source_type: "skill",
        source_id: "weather",
        input_schema: { type: "object" },
        risk_level: "low",
        requires_confirmation: false,
        enabled: true,
        available: true,
        unavailable_reason: null,
        last_error: null,
      }],
    });
    expect(toolPayload.items[0]?.available).toBe(true);

    toolListResponseSchema.parse({
      total: 2,
      items: [
        {
          tool_id: "workspace:workspace-root:workspace_read_file",
          name: "workspace_read_file",
          description: "读取工作区文件",
          source_type: "workspace",
          source_id: "workspace-root",
          input_schema: { type: "object", properties: {} },
          risk_level: "low",
          requires_confirmation: false,
          enabled: true,
          available: true,
          unavailable_reason: null,
          last_error: null,
        },
        {
          tool_id: "shell:workspace-shell:shell_execute",
          name: "shell_execute",
          description: "执行 Shell 命令",
          source_type: "shell",
          source_id: "workspace-shell",
          input_schema: { type: "object", properties: {} },
          risk_level: "high",
          requires_confirmation: true,
          enabled: true,
          available: false,
          unavailable_reason: "当前平台没有受支持的文件系统隔离后端。",
          last_error: null,
        },
      ],
    });

    const skillPayload = skillListResponseSchema.parse({
      items: [{
        name: "weather",
        description: "查询天气",
        status: "enabled",
        source: "imported",
        path: "/skills/weather",
        tools: ["weather_query"],
        can_remove: true,
        can_adopt: false,
        origin_path: null,
        installed_at: null,
        error_message: null,
        tool_name: "weather_query",
        triggers: ["天气"],
        intent_examples: [],
        input_schema: null,
        interaction_mode: "react_tool",
        requires_confirmation: false,
        compatibility: { status: "native" },
        capabilities: [],
        risk_level: "low",
        install_source: null,
        diagnostics: [],
        imported: true,
        enabled: true,
        executable: true,
        registered_as_tool: true,
        callable_by_agent: true,
        unavailable_reason: null,
      }],
    });
    expect(skillPayload.items[0]?.callable_by_agent).toBe(true);
  });

  it("校验 MCP 列表和模型脱敏配置", () => {
    const servers = mcpServerListSchema.parse([{
      server_id: "mcp-1",
      name: "搜索",
      transport_type: "streamable_http",
      command: null,
      args: [],
      url: "https://example.test/mcp",
      environment_keys: [],
      header_env_map: { Authorization: "MCP_AUTH" },
      enabled: true,
      status: "connected",
      last_connected_at: null,
      last_error: null,
      tools: [],
      resources: [],
      prompts: [],
    }]);
    expect(servers[0]?.header_env_map.Authorization).toBe("MCP_AUTH");

    const values = {
      provider: "openai_compatible",
      model_id: "fixture-model",
      base_url: "https://example.test/v1",
      api_key_configured: true,
      agno_telemetry: false,
      thinking_enabled: true,
      reasoning_effort: "high",
      context_token_budget: 4000,
    };
    const config = modelConfigResponseSchema.parse({
      config_path: "/tmp/config.toml",
      config_exists: true,
      config: values,
      effective: values,
      env_overrides: { api_key: false },
      redacted_config: { model: { api_key: "***" } },
    });
    expect(config.effective.api_key_configured).toBe(true);
    expect(JSON.stringify(config)).not.toContain("sk-secret");
  });

  it("兼容模型连接接口的数值时间戳", () => {
    const payload = modelConnectionResponseSchema.parse({
      status: "ok",
      provider: "openai_compatible",
      model_id: "fixture-model",
      detail: "模型服务连接成功。",
      duration_ms: 254,
      checked_at: 1785225742.295095,
    });
    expect(payload.duration_ms).toBe(254);
  });
});
