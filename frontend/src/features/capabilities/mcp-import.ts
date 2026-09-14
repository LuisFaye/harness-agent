import type { McpServerWritePayload } from "@/lib/api/management";

export interface McpImportItem {
  name: string;
  payload: McpServerWritePayload;
  warnings: string[];
}

export function parseMcpImport(value: string): McpImportItem[] {
  const source = value.trim();
  if (!source) {
    throw new Error("请粘贴 MCP JSON 配置。");
  }

  const parsed = parseJson(source);
  const entries = serverEntries(parsed);
  if (!entries.length) {
    throw new Error("没有识别到 MCP Server。支持 mcpServers、名称映射或单个 Server 配置。");
  }

  return entries.map(([name, config]) => toImportItem(name, config));
}

function parseJson(source: string): unknown {
  try {
    return JSON.parse(source);
  } catch {
    try {
      return JSON.parse(`{${source}}`);
    } catch {
      throw new Error("JSON 格式不正确，请检查逗号、引号和括号。");
    }
  }
}

function serverEntries(value: unknown): Array<[string, Record<string, unknown>]> {
  const root = asObject(value);
  if (!root) {
    return [];
  }

  const nested = asObject(root.mcpServers) ?? asObject(root.servers);
  if (nested) {
    return objectEntries(nested);
  }

  if (hasConnection(root)) {
    const name = text(root.name) || "新 MCP Server";
    return [[name, root]];
  }

  return objectEntries(root);
}

function objectEntries(value: Record<string, unknown>): Array<[string, Record<string, unknown>]> {
  return Object.entries(value)
    .map(([name, config]) => [name, asObject(config)] as const)
    .filter((entry): entry is [string, Record<string, unknown>] => Boolean(entry[1] && hasConnection(entry[1])));
}

function toImportItem(name: string, config: Record<string, unknown>): McpImportItem {
  const command = text(config.command);
  const url = text(config.url);
  if (!command && !url) {
    throw new Error(`${name} 缺少 command 或 url。`);
  }

  const warnings: string[] = [];
  const environmentKeys = stringArray(config.environment_keys);
  const env = asObject(config.env);
  if (env) {
    environmentKeys.push(...Object.keys(env));
    if (Object.values(env).some((item) => text(item))) {
      warnings.push("env 中的值不会导入；请通过 Agent 进程环境变量或本地 secrets 配置提供。");
    }
  }

  const headerEnvMap = stringMap(config.header_env_map);
  const headers = asObject(config.headers);
  if (headers) {
    for (const [header, rawValue] of Object.entries(headers)) {
      const envName = environmentReference(rawValue);
      if (envName) {
        headerEnvMap[header] = envName;
      } else if (text(rawValue)) {
        warnings.push(`请求头 ${header} 含明文值，出于安全原因未导入。`);
      }
    }
  }

  return {
    name,
    payload: {
      name: name.slice(0, 64),
      transport_type: url ? "streamable_http" : "stdio",
      command: url ? null : command,
      args: url ? [] : stringArray(config.args),
      url: url || null,
      environment_keys: [...new Set(environmentKeys.filter(Boolean))],
      header_env_map: headerEnvMap,
      enabled: config.enabled !== false && config.disabled !== true,
    },
    warnings: [...new Set(warnings)],
  };
}

function hasConnection(value: Record<string, unknown>): boolean {
  return Boolean(text(value.command) || text(value.url));
}

function environmentReference(value: unknown): string | null {
  const match = text(value).match(/^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$/);
  return match?.[1] ?? null;
}

function asObject(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean)
    : [];
}

function stringMap(value: unknown): Record<string, string> {
  const object = asObject(value);
  if (!object) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(object)
      .map(([key, value]) => [key.trim(), text(value)])
      .filter(([key, value]) => Boolean(key && value)),
  );
}
