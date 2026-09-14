import { describe, expect, it } from "vitest";

import { parseHeaderMap, parseLines } from "@/features/capabilities/capability-format";

import { scheduleConfigSummary, scheduleStatus } from "./schedule-format";

describe("管理页面安全格式化", () => {
  it("只从定时任务配置中提取可读摘要", () => {
    expect(scheduleConfigSummary(JSON.stringify({
      repos: ["/workspace/a", "/workspace/b"],
      author: "llh",
      extra_notes: ["不会原样展开"],
    }))).toEqual(["仓库：/workspace/a", "仓库：/workspace/b", "作者：llh"]);
    expect(scheduleConfigSummary("{broken")).toEqual(["任务配置暂时无法解析"]);
  });

  it("将任务状态映射为文字和语义色", () => {
    expect(scheduleStatus("enabled")).toEqual({ label: "已启用", tone: "success" });
    expect(scheduleStatus("paused")).toEqual({ label: "已暂停", tone: "warning" });
  });

  it("解析 MCP 行列表和 Header 环境变量映射", () => {
    expect(parseLines("one\n\n two ")).toEqual(["one", "two"]);
    expect(parseHeaderMap("Authorization=MCP_AUTH\nX-Tenant = TENANT_ID\ninvalid")).toEqual({
      Authorization: "MCP_AUTH",
      "X-Tenant": "TENANT_ID",
    });
  });
});
