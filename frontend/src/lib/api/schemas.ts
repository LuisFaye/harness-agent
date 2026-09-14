import { z } from "zod";

import {
  citationBundleSchema,
  streamEventEnvelopeSchema,
  workingStateSchema,
} from "../stream/schemas";

const nullableTimestampSchema = z.string().nullable();
const jsonObjectSchema = z.record(z.string(), z.unknown());

export const clientMessageSchema = z.object({
  role: z.enum(["user", "assistant", "system"]),
  content: z.string(),
  created_at: z.string().nullable().optional(),
});

export const askRequestSchema = z.object({
  query: z.string().min(1),
  user_id: z.string().min(1),
  session_id: z.string().min(1),
  client_messages: z.array(clientMessageSchema),
  attachment_ids: z.array(z.string()).max(10),
  suppress_user_message: z.boolean().optional(),
});

export const sessionSummarySchema = z.object({
  session_id: z.string().min(1),
  title: z.string(),
  preview: z.string(),
  message_count: z.number().int().nonnegative(),
  last_message_at: nullableTimestampSchema,
  has_summary: z.boolean(),
});

export const sessionListResponseSchema = z.object({
  items: z.array(sessionSummarySchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(100),
  offset: z.number().int().nonnegative(),
});

export const attachmentSchema = z.object({
  id: z.string().min(1),
  filename: z.string().min(1),
  content_type: z.string(),
  size: z.number().int().nonnegative(),
  suffix: z.string(),
  parse_status: z.string(),
  parse_message: z.string(),
  created_at: z.string(),
  text_preview: z.string(),
  download_url: z.string(),
  extracted_text: z.string().optional(),
});

export const attachmentListResponseSchema = z.object({
  items: z.array(attachmentSchema),
});

export const messageAttachmentViewSchema = attachmentSchema.extend({
  preview_url: z.string().optional(),
});

export const conversationMessageSchema = z.object({
  id: z.number().int(),
  session_id: z.string().min(1),
  role: z.string(),
  content: z.string(),
  created_at: nullableTimestampSchema,
  run_id: z.string().nullable(),
  attachment_ids: z.array(z.string()).optional(),
  attachments: z.array(messageAttachmentViewSchema).optional(),
}).passthrough();

export const sessionDetailResponseSchema = z.object({
  session_id: z.string().min(1),
  messages: z.array(conversationMessageSchema),
  summary: jsonObjectSchema.nullable(),
  context_observation: jsonObjectSchema.nullable(),
});

export const artifactSchema = z.object({
  artifact_id: z.string().min(32).max(32),
  user_id: z.string().min(1),
  session_id: z.string().min(1),
  run_id: z.string().min(1),
  filename: z.string().min(1),
  suffix: z.string().min(1),
  content_type: z.string().min(1),
  size: z.number().int().positive(),
  sha256: z.string().min(1),
  summary: z.string().nullable(),
  source_kind: z.enum(["generated_text", "workspace_snapshot"]),
  source_path: z.string().nullable(),
  created_at: z.string(),
  retained: z.boolean(),
  expires_at: nullableTimestampSchema,
  download_url: z.string().min(1),
});

export const artifactListResponseSchema = z.object({
  items: z.array(artifactSchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(100),
  offset: z.number().int().nonnegative(),
});

export type Artifact = z.infer<typeof artifactSchema>;
export type ArtifactListResponse = z.infer<typeof artifactListResponseSchema>;

export const runUsageSchema = z.object({
  input_tokens: z.number().int().nonnegative().nullable(),
  output_tokens: z.number().int().nonnegative().nullable(),
  total_tokens: z.number().int().nonnegative().nullable(),
  cache_read_tokens: z.number().int().nonnegative().nullable().optional(),
  cache_miss_tokens: z.number().int().nonnegative().nullable().optional(),
  cache_write_tokens: z.number().int().nonnegative().nullable().optional(),
  reasoning_tokens: z.number().int().nonnegative().nullable().optional(),
});

export const runSummarySchema = z.object({
  run_id: z.string().min(1),
  user_id: z.string().min(1),
  session_id: z.string().min(1),
  status: z.string().min(1),
  query_summary: z.string(),
  response_summary: z.string(),
  execution_source: z.string(),
  model_id: z.string().nullable(),
  error_stage: z.string().nullable(),
  started_at: nullableTimestampSchema,
  finished_at: nullableTimestampSchema,
  duration_ms: z.number().int().nonnegative().nullable(),
  total_tokens: z.number().int().nonnegative().nullable(),
  has_detail: z.boolean(),
  legacy_record: z.boolean(),
});

export const runDetailSchema = runSummarySchema.extend({
  query: z.string(),
  response_text: z.string(),
  stop_reason: z.string().nullable(),
  missing_info: z.array(z.string()),
  plan: jsonObjectSchema.nullable(),
  observations: z.array(jsonObjectSchema),
  trace: z.array(jsonObjectSchema),
  prompt_snapshot: jsonObjectSchema.nullable(),
  model_requests: z.array(jsonObjectSchema).optional().default([]),
  working_state: workingStateSchema.nullable(),
  usage: runUsageSchema,
  error_message: z.string().nullable(),
  truncated_fields: z.array(z.string()),
  artifacts: z.array(artifactSchema).optional(),
  citation_bundle: citationBundleSchema.nullable().optional(),
});

export const runListResponseSchema = z.object({
  items: z.array(runSummarySchema),
  total: z.number().int().nonnegative(),
  limit: z.number().int().min(1).max(100),
  offset: z.number().int().nonnegative(),
});

export const runEventListResponseSchema = z.object({
  run_id: z.string().min(1),
  events: z.array(streamEventEnvelopeSchema),
  last_sequence: z.number().int().nonnegative(),
  has_more: z.boolean(),
});

export const runStatusResponseSchema = z.object({
  run_id: z.string().min(1),
  state: workingStateSchema,
});

export const runCancelRequestSchema = z.object({
  user_id: z.string().min(1).max(128),
});

export const runCancelResponseSchema = z.object({
  run_id: z.string().min(1),
  status: z.string().min(1),
  accepted: z.boolean(),
  executor_notified: z.boolean(),
  message: z.string(),
});

export type AskRequest = z.infer<typeof askRequestSchema>;
export type SessionSummary = z.infer<typeof sessionSummarySchema>;
export type SessionListResponse = z.infer<typeof sessionListResponseSchema>;
export type SessionDetailResponse = z.infer<typeof sessionDetailResponseSchema>;
export type ConversationMessage = z.infer<typeof conversationMessageSchema>;
export type Attachment = z.infer<typeof attachmentSchema>;
export type RunSummary = z.infer<typeof runSummarySchema>;
export type RunDetail = z.infer<typeof runDetailSchema>;
export type RunUsage = z.infer<typeof runUsageSchema>;
export type RunListResponse = z.infer<typeof runListResponseSchema>;
export type RunEventListResponse = z.infer<typeof runEventListResponseSchema>;
export type RunCancelResponse = z.infer<typeof runCancelResponseSchema>;
