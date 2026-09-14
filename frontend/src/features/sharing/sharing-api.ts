import { z } from "zod";

import { artifactSchema, conversationMessageSchema } from "@/lib/api/schemas";
import { requestJson } from "@/lib/api/client";

const shareSummarySchema = z.object({
  id: z.string(),
  session_id: z.string(),
  permission: z.literal("read_only"),
  created_at: z.string(),
  owner_display_name: z.string(),
  owner_email: z.string().email(),
  title: z.string(),
  message_count: z.number().int().nonnegative(),
  last_message_at: z.string().nullable(),
});

const sentShareSchema = z.object({
  id: z.string(),
  session_id: z.string(),
  recipient_user_id: z.string(),
  recipient_email: z.string().email(),
  recipient_display_name: z.string(),
  permission: z.literal("read_only"),
  created_at: z.string(),
});

export type ReceivedShare = z.infer<typeof shareSummarySchema>;

export function createSessionShare(sessionId: string, recipientEmail: string) {
  return requestJson("/shares", z.object({ share: sentShareSchema }), {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, recipient_email: recipientEmail }),
  });
}

export function listReceivedShares() {
  return requestJson("/shares/received", z.object({ items: z.array(shareSummarySchema) }));
}

export function getReceivedShare(shareId: string) {
  return requestJson(
    `/shares/received/${encodeURIComponent(shareId)}`,
    z.object({
      share: z.object({
        id: z.string(),
        session_id: z.string(),
        permission: z.literal("read_only"),
        created_at: z.string(),
        owner_display_name: z.string(),
        owner_email: z.string().email(),
        title: z.string(),
        messages: z.array(conversationMessageSchema),
      }),
      artifacts: z.array(artifactSchema),
    }),
  );
}

export function copyReceivedShare(shareId: string) {
  return requestJson(
    `/shares/received/${encodeURIComponent(shareId)}/copy`,
    z.object({ status: z.string(), session_id: z.string() }),
    { method: "POST" },
  );
}
