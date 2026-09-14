import type { ZodType } from "zod";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null = null,
    public readonly retriable = false,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function apiUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${import.meta.env.DEV ? "/api" : ""}${normalized}`;
}

export async function requestJson<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetchImpl(apiUrl(path), { ...init, headers });
  if (response.status === 401 && typeof window !== "undefined") {
    window.dispatchEvent(new Event("sb-agent-auth-required"));
  }
  if (!response.ok) {
    throw await responseToApiError(response);
  }
  const payload = await response.json();
  const result = schema.safeParse(payload);
  if (!result.success) {
    throw new ApiError("服务返回的数据格式与前端契约不一致。", response.status, "invalid_response");
  }
  return result.data;
}

export async function responseToApiError(response: Response): Promise<ApiError> {
  const payload = await response.clone().json().catch(() => null) as Record<string, unknown> | null;
  const detail = payload?.error ?? payload?.detail;
  const detailObject = isRecord(detail) ? detail : null;
  const message = typeof detail === "string"
    ? detail
    : typeof detailObject?.message === "string"
      ? detailObject.message
      : `请求失败（${response.status}）`;
  const code = typeof detailObject?.code === "string" ? detailObject.code : null;
  const retriable = Boolean(detailObject?.retriable) || response.status >= 500;
  return new ApiError(message, response.status, code, retriable);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
