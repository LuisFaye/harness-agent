export const runState = {
  items: [],
  total: 0,
  limit: 20,
  offset: 0,
  selectedRunId: "",
  detail: null,
  loadingList: false,
  loadingDetail: false,
};

export function buildRunsQuery({ userId, status = "", sessionId = "", keyword = "", limit = 20, offset = 0 }) {
  const params = new URLSearchParams({
    user_id: userId || "default",
    limit: String(limit),
    offset: String(offset),
  });
  if (status.trim()) params.set("status", status.trim());
  if (sessionId.trim()) params.set("session_id", sessionId.trim());
  if (keyword.trim()) params.set("keyword", keyword.trim());
  return `/runs?${params.toString()}`;
}

export function readRunLocation(search = "") {
  const params = new URLSearchParams(search);
  return {
    view: params.get("view") || "",
    runId: params.get("run_id") || "",
    sessionId: params.get("session_id") || "",
  };
}

export function buildWorkspaceLocation(currentHref, { viewName, runId = "", sessionId = "" }) {
  const url = new URL(currentHref);
  if (viewName === "runs") {
    url.searchParams.set("view", "runs");
    if (runId) url.searchParams.set("run_id", runId);
    else url.searchParams.delete("run_id");
    url.searchParams.delete("session_id");
  } else {
    url.searchParams.delete("view");
    url.searchParams.delete("run_id");
    if (sessionId) url.searchParams.set("session_id", sessionId);
    else url.searchParams.delete("session_id");
  }
  return url;
}

export function updateWorkspaceLocation(viewName, runId = "", sessionId = "") {
  const url = buildWorkspaceLocation(window.location.href, { viewName, runId, sessionId });
  window.history.replaceState({}, "", url);
}
