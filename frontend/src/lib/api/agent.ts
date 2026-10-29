import { z } from "zod";

import {
  attachmentListResponseSchema,
  attachmentSchema,
  artifactListResponseSchema,
  runCancelResponseSchema,
  runDetailSchema,
  runEventListResponseSchema,
  runListResponseSchema,
  sessionDetailResponseSchema,
  sessionListResponseSchema,
  type AskRequest,
  type RunListResponse,
} from "./schemas";
import { apiUrl, requestJson, responseToApiError } from "./client";

const unknownObjectSchema = z.record(z.string(), z.unknown());

export interface SessionListFilters {
  keyword?: string;
  dateFrom?: string;
  dateTo?: string;
  limit?: number;
  offset?: number;
}

export function listSessions(userId: string, filters: SessionListFilters = {}) {
  const params = new URLSearchParams({
    user_id: userId,
    limit: String(filters.limit ?? 30),
    offset: String(filters.offset ?? 0),
  });
  if (filters.keyword?.trim()) {
    params.set("keyword", filters.keyword.trim());
  }
  if (filters.dateFrom) {
    params.set("date_from", filters.dateFrom);
  }
  if (filters.dateTo) {
    params.set("date_to", filters.dateTo);
  }
  return requestJson(
    `/sessions?${params.toString()}`,
    sessionListResponseSchema,
  );
}

export function getSession(userId: string, sessionId: string) {
  return requestJson(
    `/sessions/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}&limit=200`,
    sessionDetailResponseSchema,
  );
}

export function listSessionAttachments(userId: string, sessionId: string) {
  return requestJson(
    `/attachments?user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(sessionId)}`,
    attachmentListResponseSchema,
  );
}

export function listSessionArtifacts(userId: string, sessionId: string) {
  return requestJson(
    `/artifacts?user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(sessionId)}&limit=100&offset=0`,
    artifactListResponseSchema,
  );
}

export async function uploadSessionAttachment(
  userId: string,
  sessionId: string,
  file: File,
  signal?: AbortSignal,
) {
  const form = new FormData();
  form.append("file", file);
  form.append("user_id", userId);
  form.append("session_id", sessionId);
  const response = await fetch(apiUrl("/attachments"), { method: "POST", body: form, ...(signal ? { signal } : {}) });
  if (!response.ok) {
    throw await responseToApiError(response);
  }
  return attachmentSchema.parse(await response.json());
}

export function listSessionRuns(userId: string, sessionId: string, limit = 1) {
  return requestJson(
    `/runs?user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(sessionId)}&limit=${limit}&offset=0`,
    runListResponseSchema,
  );
}

export interface RunListFilters {
  status?: string;
  sessionId?: string;
  keyword?: string;
  limit?: number;
  offset?: number;
}

export function buildRunsPath(userId: string, filters: RunListFilters = {}): string {
  const params = new URLSearchParams({
    user_id: userId,
    limit: String(filters.limit ?? 20),
    offset: String(filters.offset ?? 0),
  });
  if (filters.status?.trim()) {
    params.set("status", filters.status.trim());
  }
  if (filters.sessionId?.trim()) {
    params.set("session_id", filters.sessionId.trim());
  }
  if (filters.keyword?.trim()) {
    params.set("keyword", filters.keyword.trim());
  }
  return `/runs?${params.toString()}`;
}

export function listRuns(
  userId: string,
  filters: RunListFilters = {},
): Promise<RunListResponse> {
  return requestJson(buildRunsPath(userId, filters), runListResponseSchema);
}

export function getRun(userId: string, runId: string) {
  return requestJson(
    `/runs/${encodeURIComponent(runId)}?user_id=${encodeURIComponent(userId)}`,
    runDetailSchema,
  );
}

export function getRunEvents(userId: string, runId: string, afterSequence: number) {
  return requestJson(
    `/runs/${encodeURIComponent(runId)}/events?user_id=${encodeURIComponent(userId)}&after_sequence=${afterSequence}&limit=200`,
    runEventListResponseSchema,
  );
}

export function cancelRun(userId: string, runId: string) {
  return requestJson(
    `/runs/${encodeURIComponent(runId)}/cancel`,
    runCancelResponseSchema,
    {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    },
  );
}

export function askWithoutStream(request: AskRequest) {
  return requestJson("/ask", unknownObjectSchema, {
    method: "POST",
    body: JSON.stringify(request),
  });
}


export function listRunActivity(userId: string) {
  const params = new URLSearchParams({ user_id: userId });
  return requestJson(`/runs/activity?${params}`, runListResponseSchema);
}
