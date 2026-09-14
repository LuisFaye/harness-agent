import { z } from "zod";

const nullableTextSchema = z.string().nullable();
const nullableTimestampSchema = z.string().nullable();
const jsonObjectSchema = z.record(z.string(), z.unknown());

export const scheduledJobSchema = z.object({
  id: z.number().int().positive(),
  user_id: z.string(),
  job_type: z.string(),
  title: z.string(),
  status: z.string(),
  schedule_kind: z.string(),
  schedule_time: z.string(),
  timezone: z.string(),
  config_json: nullableTextSchema,
  next_run_at: z.string(),
  last_run_at: nullableTimestampSchema,
  created_at: nullableTimestampSchema,
  updated_at: nullableTimestampSchema,
});

export const scheduledJobRunSchema = z.object({
  id: z.number().int().positive(),
  job_id: z.number().int().positive(),
  user_id: z.string(),
  status: z.string(),
  started_at: z.string(),
  finished_at: nullableTimestampSchema,
  output_text: nullableTextSchema,
  error_message: nullableTextSchema,
  metadata_json: nullableTextSchema,
  created_at: nullableTimestampSchema,
});

export const scheduleListResponseSchema = z.object({
  items: z.array(scheduledJobSchema),
});

export const scheduleRunListResponseSchema = z.object({
  items: z.array(scheduledJobRunSchema),
});

export const scheduleActionResponseSchema = z.object({
  status: z.string(),
  job_id: z.number().int().positive(),
});

export const scheduleRunNowResponseSchema = z.object({
  job: scheduledJobSchema,
  run: scheduledJobRunSchema,
  report: nullableTextSchema,
});

export const toolDefinitionSchema = z.object({
  tool_id: z.string().min(1),
  name: z.string().min(1),
  description: z.string(),
  source_type: z.enum(["skill", "mcp", "database", "workspace", "shell", "git", "artifact"]),
  source_id: z.string().min(1),
  input_schema: jsonObjectSchema,
  risk_level: z.string(),
  requires_confirmation: z.boolean(),
  enabled: z.boolean(),
  available: z.boolean(),
  unavailable_reason: nullableTextSchema,
  last_error: nullableTextSchema,
});

export const toolListResponseSchema = z.object({
  items: z.array(toolDefinitionSchema),
  total: z.number().int().nonnegative(),
});

export const skillCompatibilitySchema = z.object({
  status: z.string(),
  source_format: z.string().optional(),
  reason: z.string().optional(),
}).passthrough();

export const skillRecordSchema = z.object({
  name: z.string().min(1),
  description: z.string(),
  status: z.string(),
  source: z.string(),
  path: z.string(),
  tools: z.array(z.string()),
  can_remove: z.boolean(),
  can_adopt: z.boolean(),
  origin_path: nullableTextSchema.optional(),
  installed_at: nullableTimestampSchema.optional(),
  error_message: nullableTextSchema,
  tool_name: nullableTextSchema,
  triggers: z.array(z.string()),
  intent_examples: z.array(z.string()),
  input_schema: jsonObjectSchema.nullable(),
  interaction_mode: z.string(),
  requires_confirmation: z.boolean(),
  compatibility: skillCompatibilitySchema,
  capabilities: z.array(z.string()),
  risk_level: z.string(),
  install_source: nullableTextSchema.optional(),
  diagnostics: z.array(z.string()),
}).passthrough();

export const skillSummarySchema = skillRecordSchema.extend({
  imported: z.boolean(),
  enabled: z.boolean(),
  executable: z.boolean(),
  registered_as_tool: z.boolean(),
  callable_by_agent: z.boolean(),
  unavailable_reason: nullableTextSchema,
}).passthrough();

export const skillListResponseSchema = z.object({
  items: z.array(skillSummarySchema),
});

export const skillActionResponseSchema = z.object({
  status: z.string(),
  item: skillRecordSchema,
});

export const skillImportResponseSchema = z.object({
  status: z.string(),
  items: z.array(skillRecordSchema),
});

export const skillDeleteResponseSchema = z.object({
  status: z.string(),
  deleted: z.boolean(),
  name: z.string(),
});

export const skillFileSchema = z.object({
  path: z.string(),
  kind: z.string(),
  size_bytes: z.number().int().nonnegative(),
  content: nullableTextSchema,
  truncated: z.boolean(),
});

export const skillDetailResponseSchema = z.object({
  skill: skillSummarySchema,
  files: z.array(skillFileSchema),
  persistence: z.object({
    definition_path: z.string(),
    status_registry_path: z.string(),
    tool_registry_path: z.string(),
    source: z.string(),
  }),
});

export const mcpServerSchema = z.object({
  server_id: z.string().min(1),
  name: z.string().min(1),
  transport_type: z.enum(["stdio", "streamable_http"]),
  command: nullableTextSchema,
  args: z.array(z.string()),
  url: nullableTextSchema,
  environment_keys: z.array(z.string()),
  header_env_map: z.record(z.string(), z.string()),
  enabled: z.boolean(),
  status: z.string(),
  last_connected_at: nullableTimestampSchema,
  last_error: nullableTextSchema,
  tools: z.array(jsonObjectSchema),
  resources: z.array(jsonObjectSchema),
  prompts: z.array(jsonObjectSchema),
});

export const mcpServerListSchema = z.array(mcpServerSchema);

export const databaseConnectionSchema = z.object({
  connection_id: z.string().min(1),
  name: z.string().min(1),
  driver: z.literal("mysql"),
  host: z.string(),
  port: z.number().int().min(1).max(65535),
  database: z.string(),
  username: z.string(),
  status: z.string(),
  last_connected_at: nullableTimestampSchema,
  last_error: nullableTextSchema,
  imported: z.boolean(),
  password_configured: z.boolean(),
  scope: z.literal("agent"),
  tool_names: z.array(z.string()),
});

export const databaseConnectionListSchema = z.object({
  items: z.array(databaseConnectionSchema),
  total: z.number().int().nonnegative(),
});

export const databaseConnectionSaveSchema = z.object({
  connection: databaseConnectionSchema,
  continuation_query: nullableTextSchema,
});

export const deleteResponseSchema = z.object({
  status: z.string(),
  deleted: z.boolean(),
}).passthrough();

const modelValuesSchema = z.object({
  provider: z.string(),
  model_id: z.string(),
  base_url: z.string(),
  api_key_configured: z.boolean(),
  agno_telemetry: z.boolean(),
  thinking_enabled: z.boolean(),
  reasoning_effort: z.string(),
  context_token_budget: z.number().int().positive(),
});

export const modelConfigResponseSchema = z.object({
  status: z.string().optional(),
  config_path: z.string(),
  config_exists: z.boolean(),
  config: modelValuesSchema,
  effective: modelValuesSchema,
  env_overrides: z.record(z.string(), z.boolean()),
  redacted_config: jsonObjectSchema,
});

export const modelConnectionResponseSchema = z.object({
  status: z.string(),
  ok: z.boolean().optional(),
  provider: z.string().optional(),
  model_id: z.string().optional(),
  detail: z.string().optional(),
  message: z.string().optional(),
  error: z.string().optional(),
  duration_ms: z.number().nonnegative().optional(),
  checked_at: z.union([z.string(), z.number()]).optional(),
}).passthrough();

export const modelListResponseSchema = z.object({
  status: z.string(),
  models: z.array(z.string()),
  detail: z.string().optional(),
  duration_ms: z.number().nonnegative().optional(),
});

export type ScheduledJob = z.infer<typeof scheduledJobSchema>;
export type ScheduledJobRun = z.infer<typeof scheduledJobRunSchema>;
export type ToolDefinition = z.infer<typeof toolDefinitionSchema>;
export type SkillSummary = z.infer<typeof skillSummarySchema>;
export type SkillDetailResponse = z.infer<typeof skillDetailResponseSchema>;
export type McpServer = z.infer<typeof mcpServerSchema>;
export type DatabaseConnection = z.infer<typeof databaseConnectionSchema>;
export type ModelConfigResponse = z.infer<typeof modelConfigResponseSchema>;
