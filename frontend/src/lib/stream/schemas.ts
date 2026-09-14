import { z } from "zod";

export const STREAM_PROTOCOL_VERSION = "2.0";

export const STREAM_EVENT_TYPES = [
  "run_started",
  "status_updated",
  "trace_updated",
  "tool_started",
  "tool_finished",
  "response_started",
  "response_delta",
  "response_block",
  "response_completed",
  "run_waiting",
  "run_failed",
  "run_cancel_requested",
  "run_cancelled",
  "run_completed",
] as const;

export const streamEventTypeSchema = z.enum(STREAM_EVENT_TYPES);

const jsonObjectSchema = z.record(z.string(), z.unknown());

const timestampSchema = z.string().min(1).refine(
  (value) => !Number.isNaN(Date.parse(value)),
  "timestamp 必须是有效的 ISO 8601 时间",
);

export const workingStateBudgetSchema = z.object({
  tool_calls: z.number().int().nonnegative().optional(),
  tool_call_limit: z.number().int().positive().optional(),
  elapsed_seconds: z.number().int().nonnegative().optional(),
  max_duration_seconds: z.number().int().nonnegative().optional(),
  original_estimated_tokens: z.number().int().nonnegative().optional(),
  injected_estimated_tokens: z.number().int().nonnegative().optional(),
  saved_estimated_tokens: z.number().int().nonnegative().optional(),
}).passthrough();

export const workingStateEvidenceSchema = z.object({
  tool: z.string().nullable().optional(),
  status: z.string(),
  summary: z.string(),
  evidence_id: z.string().nullable().optional(),
}).passthrough();

export const workingStateSchema = z.object({
  run_id: z.string().min(1),
  goal_summary: z.string(),
  phase: z.string().min(1),
  current_action: z.string(),
  completed_steps: z.array(z.string()),
  evidence_summaries: z.array(workingStateEvidenceSchema),
  active_constraints: z.array(z.string()),
  blocker: z.string().nullable(),
  next_action: z.string().nullable(),
  budget: workingStateBudgetSchema,
  updated_at: z.string().nullable(),
}).passthrough();

export const traceItemSchema = z.object({
  key: z.string().min(1),
  stage: z.string().min(1),
  status: z.string().min(1),
  title: z.string(),
  summary: z.string(),
  details: jsonObjectSchema.optional(),
}).passthrough();

export const citationSourceSchema = z.object({
  source_id: z.string().min(1),
  source_type: z.literal("web"),
  title: z.string(),
  url: z.string().url().refine((value) => value.startsWith("http://") || value.startsWith("https://")),
  domain: z.string(),
  provider: z.string(),
  tool_name: z.string(),
  retrieved_at: z.string(),
  content_level: z.enum(["metadata", "snippet", "passage", "fulltext"]),
  source_role: z.enum(["primary", "secondary", "unknown"]),
  content_hash: z.string().nullable(),
  evidence_id: z.string().nullable(),
}).passthrough();

export const citationChunkSchema = z.object({
  chunk_id: z.string().min(1),
  source_id: z.string().min(1),
  text: z.string(),
  locator: jsonObjectSchema,
  content_hash: z.string(),
}).passthrough();

export const citationSupportSchema = z.object({
  support_id: z.string().min(1),
  block_id: z.string().min(1),
  answer_quote: z.string().min(1),
  target_start: z.number().int().nonnegative().nullable(),
  target_end: z.number().int().nonnegative().nullable(),
  chunk_ids: z.array(z.string().min(1)).max(3),
  support_status: z.enum(["supported", "partial", "unsupported"]),
  origin: z.enum(["provider_native", "agent_generated", "model_aligned", "deterministic"]),
}).passthrough();

export const citationBundleSchema = z.object({
  version: z.literal("1.0"),
  status: z.enum(["complete", "partial", "unavailable"]),
  answer_hash: z.string(),
  sources: z.array(citationSourceSchema),
  chunks: z.array(citationChunkSchema),
  supports: z.array(citationSupportSchema),
}).passthrough();

export const streamEventEnvelopeSchema = z.object({
  version: z.literal(STREAM_PROTOCOL_VERSION),
  run_id: z.string().trim().min(1),
  sequence: z.number().int().positive(),
  type: streamEventTypeSchema,
  timestamp: timestampSchema,
  payload: jsonObjectSchema,
}).strict();

export const streamPayloadSchemas = {
  run_started: z.object({ status: z.string().min(1) }).passthrough(),
  status_updated: z.object({ state: workingStateSchema }).passthrough(),
  trace_updated: z.object({ trace: traceItemSchema }).passthrough(),
  tool_started: jsonObjectSchema,
  tool_finished: jsonObjectSchema,
  response_started: jsonObjectSchema,
  response_delta: z.object({
    delta: z.string(),
    text: z.string().optional(),
    citation_bundle: citationBundleSchema.optional().nullable(),
  }).passthrough(),
  response_block: z.object({
    delta: z.string(),
    text: z.string().optional(),
    block: z.object({
      markdown: z.string().min(1),
      source_ids: z.array(z.string().min(1)).max(3),
    }),
    citation_bundle: citationBundleSchema.optional().nullable(),
  }).passthrough(),
  response_completed: z.object({
    text: z.string(),
    citation_bundle: citationBundleSchema.optional().nullable(),
  }).passthrough(),
  run_waiting: z.object({
    status: z.enum(["needs_input", "needs_confirmation"]),
    missing_info: z.array(z.string()),
    interaction: z.object({
      type: z.literal("database_connection"),
      request_id: z.string().min(1),
      title: z.string(),
      description: z.string(),
      name: z.string(),
      host: z.string(),
      port: z.number().int().min(1).max(65535),
      database: z.string(),
      username: z.string(),
      scope: z.literal("agent"),
    }).optional().nullable(),
  }).passthrough(),
  run_failed: z.object({
    message: z.string().min(1),
    code: z.string().optional(),
    stage: z.string().optional(),
    run_id: z.string().optional(),
    details: jsonObjectSchema.optional(),
    retriable: z.boolean().optional(),
  }).passthrough(),
  run_cancel_requested: z.object({
    status: z.literal("cancel_requested"),
  }).passthrough(),
  run_cancelled: z.object({
    status: z.literal("cancelled"),
    reason: z.string().optional(),
    result: jsonObjectSchema,
  }).passthrough(),
  run_completed: z.object({
    status: z.string().min(1),
    result: jsonObjectSchema,
  }).passthrough(),
} satisfies Record<StreamEventType, z.ZodType<Record<string, unknown>>>;

export type StreamEventType = z.infer<typeof streamEventTypeSchema>;
export type StreamEvent = z.infer<typeof streamEventEnvelopeSchema>;
export type WorkingState = z.infer<typeof workingStateSchema>;
export type TraceItem = z.infer<typeof traceItemSchema>;
export type CitationBundle = z.infer<typeof citationBundleSchema>;
export type CitationSource = z.infer<typeof citationSourceSchema>;
export type CitationChunk = z.infer<typeof citationChunkSchema>;
export type CitationSupport = z.infer<typeof citationSupportSchema>;

export function parseStreamEvent(input: unknown): StreamEvent {
  const event = streamEventEnvelopeSchema.parse(input);
  streamPayloadSchemas[event.type].parse(event.payload);
  return event;
}
