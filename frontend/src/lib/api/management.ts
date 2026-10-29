import {
  deleteResponseSchema,
  databaseConnectionListSchema,
  databaseConnectionSaveSchema,
  databaseConnectionSchema,
  mcpServerListSchema,
  mcpServerSchema,
  modelConfigResponseSchema,
  modelConnectionResponseSchema,
  modelListResponseSchema,
  scheduleActionResponseSchema,
  scheduleListResponseSchema,
  scheduleRunListResponseSchema,
  scheduleRunNowResponseSchema,
  skillActionResponseSchema,
  skillDeleteResponseSchema,
  skillDetailResponseSchema,
  skillImportResponseSchema,
  skillListResponseSchema,
  toolDefinitionSchema,
  toolListResponseSchema,
  type McpServer,
  type DatabaseConnection,
  type ModelConfigResponse,
} from "./management-schemas";
import { ApiError, requestJson } from "./client";

export function listSchedules(userId: string, limit = 100) {
  return requestJson(
    `/schedules?user_id=${encodeURIComponent(userId)}&limit=${limit}`,
    scheduleListResponseSchema,
  );
}

export function listScheduleRuns(jobId: number, limit = 20) {
  return requestJson(`/schedules/${jobId}/runs?limit=${limit}`, scheduleRunListResponseSchema);
}

export async function updateScheduleStatus(userId: string, jobId: number, action: "pause" | "resume") {
  const payload = await requestJson(
    `/schedules/${jobId}/${action}?user_id=${encodeURIComponent(userId)}`,
    scheduleActionResponseSchema,
    { method: "POST" },
  );
  if (payload.status !== "success") {
    throw new ApiError("没有找到这项自动任务。", 404, "schedule_not_found");
  }
  return payload;
}

export function runScheduleNow(userId: string, jobId: number) {
  return requestJson(
    `/schedules/${jobId}/run-now?user_id=${encodeURIComponent(userId)}`,
    scheduleRunNowResponseSchema,
    { method: "POST" },
  );
}

export function listTools() {
  return requestJson("/tools", toolListResponseSchema);
}

export function updateToolStatus(toolId: string, enabled: boolean) {
  return requestJson(
    `/tools/${encodeURIComponent(toolId)}/${enabled ? "enable" : "disable"}`,
    toolDefinitionSchema,
    { method: "POST" },
  );
}

export function listSkills() {
  return requestJson("/skills", skillListResponseSchema);
}

export function getSkillDetail(name: string) {
  return requestJson(`/skills/${encodeURIComponent(name)}/detail`, skillDetailResponseSchema);
}

export function updateSkillStatus(name: string, enabled: boolean) {
  return requestJson(
    `/skills/${encodeURIComponent(name)}/${enabled ? "enable" : "disable"}`,
    skillActionResponseSchema,
    { method: "POST" },
  );
}

export function adoptSkill(name: string) {
  return requestJson(
    `/skills/${encodeURIComponent(name)}/adopt`,
    skillActionResponseSchema,
    { method: "POST" },
  );
}

export function importSkill(path: string) {
  return requestJson("/skills/import", skillImportResponseSchema, {
    method: "POST",
    body: JSON.stringify({ path, source_url: githubSource(path), enable: true }),
  });
}

export function deleteSkill(name: string) {
  return requestJson(
    `/skills/${encodeURIComponent(name)}`,
    skillDeleteResponseSchema,
    { method: "DELETE" },
  );
}

export interface McpServerWritePayload {
  name: string;
  transport_type: McpServer["transport_type"];
  command: string | null;
  args: string[];
  url: string | null;
  environment_keys: string[];
  header_env_map: Record<string, string>;
  enabled: boolean;
}

export function listMcpServers() {
  return requestJson("/mcp/servers", mcpServerListSchema);
}

export function saveMcpServer(payload: McpServerWritePayload, serverId?: string) {
  return requestJson(
    serverId ? `/mcp/servers/${encodeURIComponent(serverId)}` : "/mcp/servers",
    mcpServerSchema,
    { method: serverId ? "PUT" : "POST", body: JSON.stringify(payload) },
  );
}

export function runMcpAction(serverId: string, action: "connect" | "disconnect" | "sync") {
  return requestJson(
    `/mcp/servers/${encodeURIComponent(serverId)}/${action}`,
    mcpServerSchema,
    { method: "POST" },
  );
}

export function deleteMcpServer(serverId: string) {
  return requestJson(
    `/mcp/servers/${encodeURIComponent(serverId)}`,
    deleteResponseSchema,
    { method: "DELETE" },
  );
}

export interface DatabaseConnectionWritePayload {
  name: string;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  interaction_request_id?: string | null;
}

export function listDatabaseConnections() {
  return requestJson("/database/connections", databaseConnectionListSchema);
}

export function saveDatabaseConnection(
  payload: DatabaseConnectionWritePayload,
  connectionId?: string,
) {
  return requestJson(
    connectionId
      ? `/database/connections/${encodeURIComponent(connectionId)}`
      : "/database/connections",
    databaseConnectionSaveSchema,
    { method: connectionId ? "PUT" : "POST", body: JSON.stringify(payload) },
  );
}

export function testDatabaseConnection(connectionId: string) {
  return requestJson(
    `/database/connections/${encodeURIComponent(connectionId)}/test`,
    databaseConnectionSchema,
    { method: "POST" },
  );
}

export function deleteDatabaseConnection(connectionId: string) {
  return requestJson(
    `/database/connections/${encodeURIComponent(connectionId)}`,
    deleteResponseSchema,
    { method: "DELETE" },
  );
}

export function getModelConfig() {
  return requestJson("/settings/model", modelConfigResponseSchema);
}

export interface ModelConfigWritePayload {
  provider: "openai_compatible" | "dashscope";
  model_id: string;
  api_key: string;
  base_url: string;
  clear_api_key: boolean;
  agno_telemetry: boolean;
  thinking_enabled: boolean;
  reasoning_effort: "high" | "max";
  context_token_budget: number;
}

export function saveModelConfig(payload: ModelConfigWritePayload): Promise<ModelConfigResponse> {
  return requestJson("/settings/model", modelConfigResponseSchema, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function testModelConnection() {
  return requestJson("/settings/model/test", modelConnectionResponseSchema, { method: "POST" });
}

export interface ModelListDraft {
  provider?: "openai_compatible" | "dashscope";
  base_url?: string;
  api_key?: string;
}

export function listAvailableModels(draft?: ModelListDraft) {
  if (!draft) return requestJson("/settings/model/available", modelListResponseSchema);
  return requestJson("/settings/model/available", modelListResponseSchema, {
    method: "POST",
    body: JSON.stringify(draft),
  });
}

function githubSource(path: string): string | null {
  const value = path.trim();
  return /^https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/?$/.test(value)
    || /^git@github\.com:[^/\s]+\/[^/\s]+(?:\.git)?$/.test(value)
    ? value
    : null;
}
