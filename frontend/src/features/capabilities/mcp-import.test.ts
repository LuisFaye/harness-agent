import { describe, expect, it } from "vitest";

import { parseMcpImport } from "./mcp-import";

describe("MCP 配置导入", () => {
  it("解析常见 mcpServers stdio 配置", () => {
    const items = parseMcpImport(JSON.stringify({
      mcpServers: {
        "mysql-agent-front": {
          command: "mysql_mcp_server_pro",
          args: ["--mode", "stdio", "--envfile", "/config/mysql.env", "--oauth", "false"],
        },
      },
    }));

    expect(items).toEqual([{
      name: "mysql-agent-front",
      payload: {
        name: "mysql-agent-front",
        transport_type: "stdio",
        command: "mysql_mcp_server_pro",
        args: ["--mode", "stdio", "--envfile", "/config/mysql.env", "--oauth", "false"],
        url: null,
        environment_keys: [],
        header_env_map: {},
        enabled: true,
      },
      warnings: [],
    }]);
  });

  it("支持直接粘贴名称片段并批量解析", () => {
    const items = parseMcpImport(`
      "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]},
      "remote": {"url": "https://example.test/mcp"}
    `);

    expect(items.map((item) => item.name)).toEqual(["filesystem", "remote"]);
    expect(items[1]?.payload.transport_type).toBe("streamable_http");
  });

  it("只导入环境变量键名和安全的 Header 映射", () => {
    const [item] = parseMcpImport(JSON.stringify({
      name: "remote",
      url: "https://example.test/mcp",
      env: { MCP_TOKEN: "secret" },
      headers: {
        Authorization: "${MCP_AUTHORIZATION}",
        "X-Plain": "secret",
      },
    }));

    expect(item?.payload.environment_keys).toEqual(["MCP_TOKEN"]);
    expect(item?.payload.header_env_map).toEqual({ Authorization: "MCP_AUTHORIZATION" });
    expect(item?.warnings).toHaveLength(2);
  });

  it("为无效 JSON 和缺少连接信息提供明确错误", () => {
    expect(() => parseMcpImport("{broken")).toThrow("JSON 格式不正确");
    expect(() => parseMcpImport(JSON.stringify({ mcpServers: { empty: { args: [] } } })))
      .toThrow("没有识别到 MCP Server");
  });
});
