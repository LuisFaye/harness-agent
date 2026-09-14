import { elements } from "./dom.js";
import { state } from "./state.js";

export function readStoredJson(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    return value == null ? fallback : JSON.parse(value);
  } catch {
    return fallback;
  }
}

export function writeStoredJson(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    return;
  }
}

export function removeStoredValue(key) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    return;
  }
}

export function parseLines(value) {
  return String(value || "")
    .split(/\n+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function showToast(message, isError = false) {
  elements.toast.hidden = false;
  elements.toast.textContent = message;
  elements.toast.style.borderColor = isError ? "#efc2c0" : "#9ad9cf";
  elements.toast.style.color = isError ? "#8b1e16" : "#0f766e";
  clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 2800);
}

export async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const helper = document.createElement("textarea");
  helper.value = text;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.opacity = "0";
  document.body.appendChild(helper);
  helper.select();
  document.execCommand("copy");
  helper.remove();
}

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function truncateText(value, maxLength) {
  const normalized = sanitizeDisplayText(value);
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}...`;
}

export function sanitizeDisplayText(value) {
  return String(value || "")
    .replace(/[┏┓┗┛┣┫┳┻╋━│─═]+/g, " ")
    .replace(/\bMessage\b/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function compactText(value, maxLength = 96) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, Math.max(0, maxLength - 3))}...`;
}

export function compactTraceValue(value, maxLength = 96) {
  if (typeof value === "string") {
    return compactText(value, maxLength);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return compactText(JSON.stringify(value), maxLength);
}

export function formatDateTime(value) {
  if (!value) return "无";
  return String(value).replace("T", " ").slice(0, 19);
}

export function formatShortTime(value) {
  if (!value) return "刚刚";
  return String(value).replace("T", " ").slice(5, 16);
}

export function formatClock(value) {
  if (!value) {
    return new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  const normalized = String(value).replace("T", " ");
  const match = normalized.match(/(\d{2}:\d{2})/);
  return match ? match[1] : normalized.slice(11, 16) || normalized;
}

export function formatElapsed(startedAt) {
  if (!startedAt) {
    return "0s";
  }
  return `${Math.max(0, Math.floor((Date.now() - startedAt) / 1000))}s`;
}

export function formatDurationMs(value) {
  const duration = Number(value || 0);
  if (duration >= 1000) {
    return `${Math.round(duration / 100) / 10}s`;
  }
  return `${duration}ms`;
}

export function latestTraceDurationText(trace) {
  const durations = (Array.isArray(trace) ? trace : [])
    .map((item) => Number(item.details?.duration_ms || 0))
    .filter((value) => value > 0);
  if (!durations.length) {
    return "0s";
  }
  return formatDurationMs(durations.reduce((sum, value) => sum + value, 0));
}

export function estimateTokens(text) {
  const value = String(text || "").trim();
  if (!value) {
    return 0;
  }
  const chinese = (value.match(/[\u3400-\u9fff]/g) || []).length;
  const latin = (value.match(/[A-Za-z0-9_./:-]+/g) || [])
    .reduce((sum, token) => sum + Math.max(1, Math.ceil(token.length / 4)), 0);
  const punctuation = (value.match(/[^\w\s\u3400-\u9fff]/g) || []).length;
  return Math.max(1, chinese + latin + punctuation);
}

export function formatNumber(value) {
  return Number(value || 0).toLocaleString("en-US");
}

export function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

export function openModal(modal) {
  modal.hidden = false;
}

export function closeModal(modal) {
  modal.hidden = true;
}
