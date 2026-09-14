import { state } from "./state.js";
import { renderStreamingText } from "./render.js";
import { renderActiveRunProgress } from "./active-run-progress.js";
import { formatElapsed } from "./utils.js";

export function queueResponseDelta(delta, onFlush) {
  const run = state.activeRun;
  if (!run || !delta) {
    return;
  }
  run.pendingDeltas.push(delta);
  if (run.deltaFrame == null) {
    run.deltaFrame = window.requestAnimationFrame(() => flushPendingDeltas(onFlush));
  }
}

export function flushPendingDeltas(onFlush) {
  const run = state.activeRun;
  if (!run) {
    return;
  }
  if (run.deltaFrame != null) {
    window.cancelAnimationFrame(run.deltaFrame);
    run.deltaFrame = null;
  }
  if (!run.pendingDeltas.length) {
    return;
  }
  run.streamedText = `${run.streamedText || ""}${run.pendingDeltas.join("")}`;
  run.pendingDeltas = [];
  state.latestResultPayload = {
    ...(state.latestResultPayload || {}),
    response_text: run.streamedText,
  };
  if (onFlush) {
    onFlush();
  }
}

export function updateStableRunCard(card, run) {
  const progress = card.querySelector(".active-run-progress");
  const nextProgress = renderActiveRunProgress(run);
  const signature = nextProgress.match(/data-progress-signature="([^"]+)"/)?.[1] || "";
  if (progress && progress.dataset.progressSignature !== signature) {
    progress.outerHTML = nextProgress;
  }
  card.classList.toggle("has-response", Boolean(run.streamedText));
  const body = card.querySelector(".streaming-response");
  if (body) {
    updateStreamingResponseBody(body, run);
  }
  const elapsed = card.querySelector("[data-run-elapsed]");
  if (elapsed) {
    elapsed.textContent = formatElapsed(run.startedAt);
  }
}

export function updateStreamingResponseBody(body, run) {
  const hasContent = Boolean(run.streamedText);
  body.classList.toggle("has-content", hasContent);
  body.classList.toggle("is-streaming", hasContent && !run.responseCompleted);
  if (!hasContent) {
    body.textContent = "";
    return;
  }
  body.innerHTML = renderStreamingText(run.streamedText, "");
}
