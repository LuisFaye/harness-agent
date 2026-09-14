import { elements } from "./dom.js";
import { state } from "./state.js";
import {
  compactText,
  compactTraceValue,
  cssEscape,
  escapeHtml,
  estimateTokens,
  formatClock,
  formatDurationMs,
  formatElapsed,
  formatNumber,
} from "./utils.js";
import { renderStreamingText } from "./render.js";
import { renderConfirmationActions } from "./process-view.js";
import { renderActiveRunProgress } from "./active-run-progress.js";
import { composerTextFromStatus, runCardStatusText } from "./run-status.js";
import { followChatIfNeeded } from "./chat-scroll.js";
import { refreshLiveRunDrawer } from "./run-drawer.js";
import {
  flushPendingDeltas,
  queueResponseDelta,
  updateStableRunCard,
} from "./stream-render.js";
import { initializeWorkingState, updateWorkingState } from "./status-bar.js";
export function normalizeTraceItem(item, index) {
  return {
    key: item.key || `${item.stage || "trace"}-${index}`,
    stage: item.stage || "final",
    status: item.status || "success",
    title: item.title || "执行事件",
    summary: item.summary || "",
    details: item.details || {},
  };
}
export function updatePromptSnapshot(snapshot) {
  if (snapshot) {
    state.latestPromptSnapshot = snapshot;
  }
  renderPromptSnapshot();
}
export function renderPromptSnapshot() {
  const snapshot = state.latestPromptSnapshot;
  if (!snapshot) {
    elements.promptMeta.textContent = "等待本轮请求生成 Prompt。";
    elements.promptOutput.textContent = "等待请求";
    return;
  }
  const toolCount = Array.isArray(snapshot.tool_names) ? snapshot.tool_names.length : 0;
  const skillCount = Array.isArray(snapshot.skill_names) ? snapshot.skill_names.length : 0;
  const instructionCount = Array.isArray(snapshot.instructions) ? snapshot.instructions.length : 0;
  const modelText = snapshot.model_id || "模型未知";
  const budgetText = snapshot.context_token_budget == null ? "未知预算" : `${snapshot.context_token_budget} tokens`;
  elements.promptMeta.textContent = `${modelText} · ${budgetText} · ${instructionCount} 段 instructions · ${toolCount} 个工具 · ${skillCount} 个技能`;
  elements.promptOutput.textContent = snapshot.prompt_text || buildPromptTextFallback(snapshot);
  setOptionalText(elements.runModelSummary, modelText);
}
export function buildPromptTextFallback(snapshot) {
  const lines = [
    "## Instructions",
    Array.isArray(snapshot.instructions) && snapshot.instructions.length ? snapshot.instructions.join("\n\n") : "无",
    "## User Input",
    snapshot.user_input || "无",
  ];
  return lines.join("\n\n");
}
export function renderDeveloperRawJson() {
  const payload = state.latestResultPayload || {
    prompt_snapshot: state.latestPromptSnapshot,
    trace: state.latestTrace,
  };
  elements.developerRawJson.textContent = JSON.stringify(payload, null, 2);
}
export function renderUsageSummary(payload = state.latestResultPayload) {
  const snapshot = payload?.prompt_snapshot || state.latestPromptSnapshot || {};
  const responseText = payload?.response_text || "";
  const hasPromptSnapshot = Boolean(
    snapshot.prompt_text ||
    snapshot.user_input ||
    (Array.isArray(snapshot.instructions) && snapshot.instructions.length)
  );
  const promptText = hasPromptSnapshot ? (snapshot.prompt_text || buildPromptTextFallback(snapshot)) : "";
  const inputTokens = promptText.trim() ? estimateTokens(promptText) : null;
  const outputTokens = responseText.trim() ? estimateTokens(responseText) : null;
  const totalTokens = inputTokens == null && outputTokens == null ? null : (inputTokens || 0) + (outputTokens || 0);
  setOptionalText(elements.usageInputTokens, inputTokens == null ? "--" : formatNumber(inputTokens));
  setOptionalText(elements.usageOutputTokens, outputTokens == null ? "--" : formatNumber(outputTokens));
  setOptionalText(elements.usageTotalTokens, totalTokens == null ? "--" : formatNumber(totalTokens));
}
export function renderTrace(trace) {
  state.latestTrace = Array.isArray(trace) ? trace : [];
  renderExecutionTimeline(state.latestTrace);
  if (!state.latestTrace.length) {
    elements.chatTrace.innerHTML = `<div class="trace-empty">等待请求</div>`;
    return;
  }
  elements.chatTrace.innerHTML = state.latestTrace.map((item, index) => renderTraceItem(item, index)).join("");
}
export function renderExecutionTimeline(trace) {
  if (!elements.executionTimeline) {
    return;
  }
  const items = summarizeRunTrace(trace || []);
  if (!items.length) {
    elements.executionTimeline.innerHTML = `
      <div class="execution-step pending">
        <span></span>
        <strong>等待任务</strong>
        <em>0s</em>
      </div>
    `;
    return;
  }
  elements.executionTimeline.innerHTML = items.map((item) => {
    const status = item.status === "running" ? "running" : item.status === "error" ? "error" : "done";
    const duration = item.details?.duration_ms != null ? formatDurationMs(item.details.duration_ms) : traceStatusText(item.status);
    return `
      <button type="button" class="execution-step ${escapeHtml(status)}" data-execution-step="trace">
        <span></span>
        <strong>${escapeHtml(item.title || traceStageText(item.stage))}</strong>
        <em>${escapeHtml(duration)}</em>
      </button>
    `;
  }).join("");
}
export function resetExecutionPanels(statusText = "未开始") {
  state.latestTrace = [];
  state.latestPromptSnapshot = null;
  state.latestResultPayload = null;
  state.activeRunStartedAt = null;
  setOptionalText(elements.chatLastStatus, statusText);
  setOptionalText(elements.chatMissing, "无");
  elements.chatPlan.textContent = "等待请求";
  elements.chatObservations.textContent = "等待请求";
  renderTrace([]);
  renderUsageSummary();
  renderPromptSnapshot();
  renderDeveloperRawJson();
  updateComposerState();
}
export function startActiveRun(query) {
  const startedAt = Date.now();
  const submittedTrace = {
    key: "submitted",
    stage: "loop_started",
    status: "running",
    title: "请求已提交",
    summary: "已收到你的消息，正在连接执行链路。",
    details: {},
  };
  state.latestPromptSnapshot = null;
  initializeWorkingState(query);
  state.latestResultPayload = { status: "running", query, trace: [], observations: [] };
  state.activeRun = {
    id: `run-${startedAt}`,
    runId: "",
    lastSequence: 0,
    query,
    startedAt,
    status: "running",
    streamedText: "",
    responseStarted: false,
    responseCompleted: false,
    pendingDeltas: [],
    deltaFrame: null,
    reconnecting: false,
    cancelRequested: false,
    trace: [submittedTrace],
    activityItems: [],
    currentActivityKey: null,
  };
  state.activeRunStartedAt = startedAt;
  elements.summaryBanner.hidden = true;
  elements.summaryBanner.open = false;
  addOrUpdateRunActivity(state.activeRun, submittedTrace);
  setOptionalText(elements.chatLastStatus, "执行中");
  setOptionalText(elements.chatMissing, "无");
  elements.chatPlan.textContent = "正在生成计划...";
  elements.chatObservations.textContent = "等待工具返回...";
  setComposerState("running", currentRunActivity(state.activeRun)?.text || "正在处理你的请求");
  renderPromptSnapshot();
  renderDeveloperRawJson();
  renderTrace(state.activeRun.trace);
  updateComposerState();
  renderActiveRunCard();
  updateActiveRunRuntime();
  clearInterval(state.activeRunTimer);
  state.activeRunTimer = window.setInterval(updateActiveRunRuntime, 1000);
}
export function upsertLiveTrace(item) {
  if (!state.activeRun) {
    return;
  }
  const traceItem = normalizeTraceItem(item, state.activeRun.trace.length);
  if (traceItem.details?.prompt_snapshot) {
    updatePromptSnapshot(traceItem.details.prompt_snapshot);
  }
  const existingIndex = state.activeRun.trace.findIndex((entry) => entry.key === traceItem.key);
  if (existingIndex >= 0) {
    state.activeRun.trace.splice(existingIndex, 1, traceItem);
  } else {
    state.activeRun.trace.push(traceItem);
  }
  addOrUpdateRunActivity(state.activeRun, traceItem);
  state.activeRun.status = traceItem.status === "error" ? "error" : state.activeRun.status;
  state.latestResultPayload = {
    ...(state.latestResultPayload || {}),
    status: state.activeRun.status,
    query: state.activeRun.query,
    trace: state.activeRun.trace,
  };
  renderTrace(state.activeRun.trace);
  renderDeveloperRawJson();
  updateComposerState();
  renderActiveRunCard();
}
export function appendResponseDelta(delta) {
  if (!state.activeRun || !delta) {
    return;
  }
  state.activeRun.responseStarted = true;
  ensureResponseActivity(state.activeRun);
  queueResponseDelta(delta, renderAfterDeltaFlush);
}
export function replaceStreamingResponse(text) {
  if (!state.activeRun) {
    return;
  }
  flushPendingDeltas(renderAfterDeltaFlush);
  state.activeRun.responseStarted = Boolean(String(text || "").trim());
  if (state.activeRun.responseStarted) {
    ensureResponseActivity(state.activeRun);
  }
  state.activeRun.streamedText = text || "";
  state.latestResultPayload = {
    ...(state.latestResultPayload || {}),
    response_text: state.activeRun.streamedText,
  };
  renderUsageSummary(state.latestResultPayload);
  updateComposerState();
  renderActiveRunCard();
}
export function finishActiveRun(payload) {
  if (!state.activeRun) {
    applyChatResult(payload);
    return;
  }
  flushPendingDeltas(renderAfterDeltaFlush);
  updateWorkingState(payload.working_state || state.latestWorkingState);
  state.activeRun.status = payload.status || "success";
  state.activeRun.runId = payload.run_id || "";
  state.activeRun.trace = Array.isArray(payload.trace) ? payload.trace : state.activeRun.trace;
  state.activeRun.responseStarted = true;
  state.activeRun.responseCompleted = true;
  state.activeRun.streamedText = payload.response_text || state.activeRun.streamedText || "";
  rebuildRunActivityFromTrace(state.activeRun);
  completeResponseActivity(state.activeRun, payload);
  applyChatResult(payload);
  renderActiveRunCard({ completed: true });
  stopActiveRunTimer();
  setComposerState(payload.status === "success" ? "success" : payload.status === "error" ? "error" : "idle", composerTextFromStatus(payload.status));
  updateComposerState({ completed: true });
  state.activeRunStartedAt = null;
  state.activeRun = null;
  state.currentAbortController = null;
}
export function failActiveRun(error) {
  const fallbackTrace = {
    key: "final",
    stage: "error",
    status: "error",
    title: "请求失败",
    summary: error.message,
    details: {},
  };
  if (state.activeRun) {
    state.activeRun.status = "error";
    upsertLiveTrace(fallbackTrace);
    renderActiveRunCard({ completed: true });
  }
  setOptionalText(elements.chatLastStatus, "error");
  setOptionalText(elements.chatMissing, "无");
  elements.chatPlan.textContent = "请求失败";
  elements.chatObservations.textContent = "没有拿到执行观察。";
  renderTrace([fallbackTrace]);
  stopActiveRunTimer();
  setComposerState("error", "这轮请求失败了，可以修改后重试");
  updateComposerState({ completed: true });
  state.activeRunStartedAt = null;
  state.activeRun = null;
  state.currentAbortController = null;
}
export function markActiveRunStopped() {
  const stoppedTrace = {
    key: "final",
    stage: "error",
    status: "error",
    title: "已停止接收结果",
    summary: "浏览器已停止读取本轮响应，但后台任务可能仍在继续。",
    details: {},
  };
  if (state.activeRun) {
    state.activeRun.status = "error";
    upsertLiveTrace(stoppedTrace);
    renderActiveRunCard({ completed: true });
  }
  setOptionalText(elements.chatLastStatus, "stopped");
  setOptionalText(elements.chatMissing, "无");
  elements.chatPlan.textContent = "已停止";
  elements.chatObservations.textContent = "浏览器已停止接收结果；后台是否停止要等 M2 完成真正取消。";
  renderTrace([stoppedTrace]);
  stopActiveRunTimer();
  setComposerState("idle", "已停止接收结果，后台任务可能仍在继续");
  updateComposerState({ completed: true });
  state.activeRunStartedAt = null;
  state.activeRun = null;
  state.currentAbortController = null;
}
export function markCancelRequested(message = "正在等待当前执行步骤结束") {
  if (!state.activeRun) {
    return;
  }
  state.activeRun.cancelRequested = true;
  state.activeRun.status = "cancel_requested";
  setOptionalText(elements.chatLastStatus, "cancel_requested");
  setComposerState("running", message);
  updateComposerState();
  renderActiveRunCard();
}
export function markStreamReconnecting() {
  if (!state.activeRun) {
    return;
  }
  state.activeRun.reconnecting = true;
  setComposerState("running", "连接中断，正在恢复运行事件...");
}
export function applyChatResult(payload) {
  state.latestResultPayload = payload || null;
  syncAssistantMessageToClientState(payload);
  updatePromptSnapshot(payload?.prompt_snapshot || state.latestPromptSnapshot);
  setOptionalText(elements.chatLastStatus, payload.status);
  setOptionalText(elements.chatMissing, payload.missing_info?.length ? payload.missing_info.join("、") : "无");
  elements.chatPlan.innerHTML = renderPlanPanel(payload.plan);
  elements.chatObservations.innerHTML = renderObservationsPanel(payload.observations || []);
  renderTrace(payload.trace || []);
  renderUsageSummary(payload);
  renderDeveloperRawJson();
}
function syncAssistantMessageToClientState(payload) {
  const content = String(payload?.response_text || "").trim();
  if (!content) {
    return;
  }
  const lastMessage = state.messages[state.messages.length - 1];
  if (lastMessage?.role === "assistant" && String(lastMessage.content || "").trim() === content) {
    return;
  }
  state.messages.push({
    role: "assistant",
    content,
    created_at: new Date().toISOString(),
    run_id: payload?.run_id || null,
  });
}
export function stopActiveRunTimer() {
  clearInterval(state.activeRunTimer);
  state.activeRunTimer = null;
  elements.composerRuntime.textContent = "0s";
}
export function updateActiveRunRuntime() {
  if (!state.activeRun) {
    elements.composerRuntime.textContent = "0s";
    setOptionalText(elements.rightElapsed, "0s");
    return;
  }
  const seconds = Math.max(0, Math.floor((Date.now() - state.activeRun.startedAt) / 1000));
  elements.composerRuntime.textContent = `${seconds}s`;
  setOptionalText(elements.rightElapsed, `${seconds}s`);
  const footer = document.querySelector(`#${cssEscape(state.activeRun.id)} [data-run-elapsed]`);
  if (footer) {
    footer.textContent = `${seconds}s`;
  }
}
export function setComposerState(mode, text) {
  elements.composerStatusText.textContent = text;
  elements.composerStatusDot.className = "status-dot";
  if (["running", "success", "error"].includes(mode)) {
    elements.composerStatusDot.classList.add(mode);
  }
}
export function renderActiveRunCard({ completed = false } = {}) {
  if (!state.activeRun) {
    return;
  }
  const existing = document.getElementById(state.activeRun.id);
  const html = renderActiveRunHtml(state.activeRun, { completed });
  if (existing) {
    if (completed) {
      existing.outerHTML = html;
    } else {
      updateStableRunCard(existing, state.activeRun);
    }
  } else {
    if (elements.chatLog.querySelector(".empty-state")) {
      elements.chatLog.innerHTML = "";
    }
    elements.chatLog.insertAdjacentHTML("beforeend", html);
  }
  refreshLiveRunDrawer(state.activeRun);
  followChatIfNeeded();
}
function renderAfterDeltaFlush() {
  renderUsageSummary(state.latestResultPayload);
  updateComposerState();
  renderActiveRunCard();
}
function updateComposerState({ completed = false } = {}) {
  const run = state.activeRun;
  const current = currentRunActivity(run);
  if (run && !completed && run.status === "running" && current?.text) {
    setComposerState("running", current.text);
  }
}
function addOrUpdateRunActivity(run, traceItem) {
  const activity = activityFromTraceItem(traceItem);
  if (!activity) {
    return;
  }
  activity.title = traceItem.title || activity.meta || "执行步骤";
  activity.details = traceItem.details || {};
  run.activityItems = Array.isArray(run.activityItems) ? run.activityItems : [];
  const existingIndex = run.activityItems.findIndex((item) => item.key === activity.key);
  if (existingIndex >= 0) {
    run.activityItems.splice(existingIndex, 1, {
      ...run.activityItems[existingIndex],
      ...activity,
      updatedAt: Date.now(),
    });
  } else {
    run.activityItems.push({ ...activity, createdAt: Date.now(), updatedAt: Date.now() });
  }
  run.currentActivityKey = activity.key;
}
function ensureResponseActivity(run) {
  run.activityItems = Array.isArray(run.activityItems) ? run.activityItems : [];
  const finalItem = run.activityItems.find((item) => item.key === "final");
  if (finalItem?.status === "running") {
    finalItem.text = "开始流式输出回复...";
    finalItem.updatedAt = Date.now();
    run.currentActivityKey = finalItem.key;
    return;
  }
  addOrUpdateSyntheticActivity(run, {
    key: "response-stream",
    status: "running",
    stage: "final_response",
    text: "开始流式输出回复...",
    meta: "回复",
  });
}
function completeResponseActivity(run, payload) {
  const status = payload.status || run.status || "success";
  const text = status === "success"
    ? "回复已生成，可以继续追问。"
    : composerTextFromStatus(status);
  const finalItem = [...(run.activityItems || [])].reverse().find((item) => traceSemanticStage(item.stage) === "final");
  addOrUpdateSyntheticActivity(run, {
    key: finalItem?.key || "response-stream",
    status,
    stage: "final_response",
    text,
    meta: "回复",
  });
}
function addOrUpdateSyntheticActivity(run, activity) {
  run.activityItems = Array.isArray(run.activityItems) ? run.activityItems : [];
  const existingIndex = run.activityItems.findIndex((item) => item.key === activity.key);
  if (existingIndex >= 0) {
    run.activityItems.splice(existingIndex, 1, {
      ...run.activityItems[existingIndex],
      ...activity,
      updatedAt: Date.now(),
    });
  } else {
    run.activityItems.push({ ...activity, createdAt: Date.now(), updatedAt: Date.now() });
  }
  run.currentActivityKey = activity.key;
}
function rebuildRunActivityFromTrace(run) {
  const items = Array.isArray(run.trace) ? run.trace : [];
  run.activityItems = [];
  run.currentActivityKey = null;
  items.forEach((item, index) => addOrUpdateRunActivity(run, normalizeTraceItem(item, index)));
}
function currentRunActivity(run) {
  const items = Array.isArray(run?.activityItems) ? run.activityItems : [];
  if (!items.length) {
    return null;
  }
  const explicit = items.find((item) => item.key === run.currentActivityKey);
  if (explicit) {
    return explicit;
  }
  const running = [...items].reverse().find((item) => item.status === "running");
  return running || explicit || items[items.length - 1];
}
export function activityFromTraceItem(item) {
  const details = item.details || {};
  const status = item.status || "success";
  const key = item.key || `${item.stage || "trace"}-${Date.now()}`;
  const semanticStage = traceSemanticStage(item.stage);
  if (key === "submitted") {
    return {
      key,
      status: "running",
      stage: "loop_started",
      text: "已收到消息，正在连接执行链路...",
      meta: "提交",
    };
  }
  if (semanticStage === "prompt") {
    const skillCount = details.skill_count || 0;
    return {
      key,
      status,
      stage: item.stage,
      text: status === "running"
        ? `正在整理上下文和 Prompt，当前可用 ${skillCount} 个 skill...`
        : `上下文和 Prompt 已整理完成，可用 ${skillCount} 个 skill。`,
      meta: details.model_id || "Prompt",
    };
  }
  if (item.stage === "memory") {
    const count = Array.isArray(details.items) ? details.items.length : 0;
    return {
      key,
      status,
      stage: item.stage,
      text: count
        ? `从这次输入里提取到 ${count} 条可复用记忆。`
        : status === "running" ? "正在检查是否有可复用记忆..." : "记忆检查已完成，没有新增记忆。",
      meta: "记忆",
    };
  }
  if (semanticStage === "routing") {
    return {
      key,
      status,
      stage: item.stage,
      text: routeActivityText(details, item.summary),
      meta: routeText(details.route || "routing"),
    };
  }
  if (semanticStage === "planning") {
    const stepCount = Array.isArray(details.steps) ? details.steps.length : 0;
    const target = details.skill_name || details.intent || "任务";
    return {
      key,
      status,
      stage: item.stage,
      text: status === "running"
        ? stepCount ? `正在把 ${target} 拆成 ${stepCount} 个执行步骤...` : `正在为 ${target} 生成执行计划...`
        : stepCount ? `${target} 已拆成 ${stepCount} 个执行步骤。` : `${target} 的执行计划已生成。`,
      meta: "计划",
    };
  }
  if (semanticStage === "reasoning") {
    return {
      key,
      status,
      stage: item.stage,
      text: status === "running" ? "Agent 正在思考..." : "Agent 已完成思考。",
      meta: "思考",
    };
  }
  if (semanticStage === "iteration") {
    const iteration = Number(details.iteration) || 1;
    const text = item.stage === "iteration_started"
      ? `第 ${iteration} 轮正在选择下一步操作...`
      : item.stage === "iteration_observed"
        ? `第 ${iteration} 轮工具结果已返回，正在判断是否继续...`
        : `第 ${iteration} 轮判断完成，正在继续处理...`;
    return { key, status, stage: item.stage, text, meta: `第 ${iteration} 轮` };
  }
  if (semanticStage === "tool") {
    const toolText = details.skill_name
      ? `${details.skill_name} skill`
      : details.tool || item.title || "工具";
    const duration = details.duration_ms != null ? `，耗时 ${formatDurationMs(details.duration_ms)}` : "";
    if (status === "running") {
      return {
        key,
        status,
        stage: item.stage,
        text: `正在调用 ${toolText}...`,
        meta: details.tool || "工具",
      };
    }
    if (status === "error") {
      return {
        key,
        status,
        stage: item.stage,
        text: `${toolText} 调用失败${duration}。`,
        meta: details.tool || "工具",
      };
    }
    return {
      key,
      status,
      stage: item.stage,
      text: `${toolText} 已返回结果${duration}。`,
      meta: details.tool || "工具",
    };
  }
  if (semanticStage === "input") {
    const missing = Array.isArray(details.missing_info) ? details.missing_info.join("、") : "";
    const meta = item.stage === "waiting_confirmation" ? "确认" : "补充信息";
    return {
      key,
      status,
      stage: item.stage,
      text: missing ? `还需要你补充：${missing}` : item.summary || "需要你补充信息后继续。",
      meta,
    };
  }
  if (semanticStage === "final") {
    if (status === "running") {
      return {
        key,
        status,
        stage: item.stage,
        text: details.observation_count ? "工具结果已返回，正在整理最终回复..." : "正在直接整理回复...",
        meta: "回复",
      };
    }
    if (status === "error") {
      return {
        key,
        status,
        stage: item.stage,
        text: item.summary || "执行中断了。",
        meta: "回复",
      };
    }
    return {
      key,
      status,
      stage: item.stage,
      text: "回复已生成，可以继续追问。",
      meta: "回复",
    };
  }
  return {
    key,
    status,
    stage: item.stage,
    text: status === "running" ? "正在处理当前步骤..." : "当前步骤已完成。",
    meta: traceStageText(item.stage),
  };
}
function routeActivityText(details, fallback) {
  if (details.route === "skill" || details.route === "skill_command") {
    const skill = details.skill_name || details.tool_name || "对应";
    if (details.is_continuation) {
      return `检测到上次未完成的 ${skill} skill，准备继续执行...`;
    }
    return `命中 ${skill} skill，正在准备调用...`;
  }
  if (details.route === "react" || details.route === "agent_loop") {
    return "交给模型自主判断是否需要调用工具...";
  }
  if (details.route === "error") {
    return fallback || "Agent loop 未能启动。";
  }
  if (details.route === "planner") {
    return "正在走规划链路，先拆步骤再执行...";
  }
  return fallback || "正在判断这次任务该走哪条链路...";
}
function renderActiveRunHtml(run, { completed = false } = {}) {
  const statusText = completed ? runCardStatusText(run.status) : "正在处理";
  const revealResponse = Boolean(run.streamedText);
  const responseContent = renderStreamingText(run.streamedText, "");
  const detailButton = completed && run.runId
    ? `<button type="button" data-run-id="${escapeHtml(run.runId)}">查看过程</button>`
    : "";
  const downloadButton = completed && revealResponse
    ? `<button type="button" data-active-message-action="download">下载 .md</button>`
    : "";
  const confirmationHtml = completed
    ? renderConfirmationActions(run.status, run.runId)
    : "";
  return `
    <article class="message-row assistant active-run ${revealResponse ? "has-response" : ""}" id="${escapeHtml(run.id)}">
      <div class="message-content">
        <div class="message-meta">
          <strong>Agent</strong>
          <span>${formatClock(new Date().toISOString())}</span>
        </div>
        ${completed ? "" : renderActiveRunProgress(run)}
        <div class="message-body streaming-response ${revealResponse ? "has-content" : ""} ${revealResponse && !run.responseCompleted ? "is-streaming" : ""}">
          ${revealResponse ? responseContent : ""}
        </div>
        ${confirmationHtml}
        ${completed ? `<div class="message-run-summary">
          <span><i aria-hidden="true"></i>${escapeHtml(statusText)} · ${run.activityItems?.length || 0} 个步骤 · <strong data-run-elapsed>${formatElapsed(run.startedAt)}</strong></span>
          ${downloadButton}
          ${detailButton}
        </div>` : ""}
      </div>
    </article>
  `;
}
function setOptionalText(element, value) {
  if (element) element.textContent = value;
}
function renderTraceItem(item, index) {
  const insights = buildTraceInsights(item);
  return `
    <article class="trace-item trace-status-${escapeHtml(item.status || "unknown")}">
      <div class="trace-item-head">
        <div class="trace-title-group">
          <span class="trace-index">${index + 1}</span>
          <div>
            <strong>${escapeHtml(item.title || "执行事件")}</strong>
            <p>${escapeHtml(item.summary || "")}</p>
          </div>
        </div>
        <div class="trace-badges">
          <span class="trace-badge">${escapeHtml(traceStageText(item.stage))}</span>
          <span class="trace-badge">${escapeHtml(traceStatusText(item.status))}</span>
        </div>
      </div>
      ${renderTraceDetails(item, insights)}
    </article>
  `;
}
function renderTraceDetails(item, insights) {
  const details = item.details || {};
  const entries = Object.entries(details).filter(([key, value]) => (
    key !== "prompt_snapshot" && value != null && value !== "" && (!Array.isArray(value) || value.length)
  ));
  if (!entries.length && !insights.length) {
    return "";
  }
  const insightHtml = insights.length
    ? `<div class="trace-insight-grid">${insights.map((entry) => `
        <div class="trace-insight"><span>${escapeHtml(entry.label)}</span><strong>${escapeHtml(entry.value)}</strong></div>
      `).join("")}</div>`
    : "";
  const rawHtml = entries.length
    ? `<details class="trace-raw">
        <summary>查看原始详情</summary>
        ${entries.map(([key, value]) => `
          <div class="trace-detail-row">
            <span>${escapeHtml(traceDetailLabel(key))}</span>
            <pre class="trace-detail-value">${escapeHtml(formatTraceValue(value))}</pre>
          </div>
        `).join("")}
      </details>`
    : "";
  return `<div class="trace-details-wrap">${insightHtml}${rawHtml}</div>`;
}
function renderPlanPanel(plan) {
  if (!plan) {
    return `<div class="run-info-empty">等待请求</div>`;
  }
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const missingInfo = Array.isArray(plan.missing_info) ? plan.missing_info.filter(Boolean) : [];
  return `
    <div class="plan-summary">
      <div class="trace-insight"><span>意图</span><strong>${escapeHtml(plan.intent || "unknown")}</strong></div>
      <div class="trace-insight"><span>风险</span><strong>${escapeHtml(plan.risk_level || "unknown")}</strong></div>
      <div class="trace-insight"><span>缺参</span><strong>${escapeHtml(missingInfo.length ? missingInfo.join("、") : "无")}</strong></div>
    </div>
    <div class="plan-step-list">
      ${steps.length ? steps.map((step, index) => `
        <article class="plan-step-card">
          <span class="trace-index">${index + 1}</span>
          <div>
            <strong>${escapeHtml(step.name || "未命名步骤")}</strong>
            <p>${escapeHtml(step.reason || "无说明")}</p>
          </div>
        </article>
      `).join("") : `<div class="run-info-empty">没有规划步骤。</div>`}
    </div>
  `;
}
function renderObservationsPanel(observations) {
  if (!Array.isArray(observations) || !observations.length) {
    return `<div class="run-info-empty">本次没有工具观察。</div>`;
  }
  return observations.map((item, index) => `
    <article class="observation-card status-${escapeHtml(item.status || "unknown")}">
      <div class="observation-head">
        <span class="trace-index">${index + 1}</span>
        <div>
          <strong>${escapeHtml(item.name || "未命名步骤")}</strong>
          <p>${escapeHtml(item.tool || "无工具")} · ${escapeHtml(item.status || "unknown")}</p>
        </div>
      </div>
      <div class="run-stage-meta">
        <span>耗时 ${escapeHtml(String(item.duration_ms || 0))}ms</span>
        <span>尝试 ${escapeHtml(String(item.attempts || 0))} 次</span>
      </div>
      ${item.output_summary ? `<div class="observation-section"><span>输出</span><p>${escapeHtml(compactText(item.output_summary, 220))}</p></div>` : ""}
      ${item.error_message ? `<div class="observation-section"><span>错误</span><p>${escapeHtml(item.error_message)}</p></div>` : ""}
    </article>
  `).join("");
}
function summarizeRunTrace(trace) {
  const items = Array.isArray(trace) ? trace : [];
  if (items.length <= 4) {
    return items;
  }
  const prompt = items.find((item) => traceSemanticStage(item.stage) === "prompt");
  const toolItems = items.filter((item) => traceSemanticStage(item.stage) === "tool");
  const finalItem = [...items].reverse().find((item) => traceSemanticStage(item.stage) === "final");
  const selected = [
    items.find((item) => traceSemanticStage(item.stage) === "routing"),
    prompt,
    toolItems.length ? { ...toolItems[toolItems.length - 1], summary: `已记录 ${toolItems.length} 次工具事件。` } : null,
    finalItem || items[items.length - 1],
  ].filter(Boolean);
  return selected.filter((item, index, all) => all.findIndex((entry) => entry.key === item.key) === index);
}
function buildTraceInsights(item) {
  const details = item.details || {};
  const insights = [];
  const semanticStage = traceSemanticStage(item.stage);
  const add = (label, value) => {
    if (value == null || value === "" || (Array.isArray(value) && !value.length)) return;
    insights.push({ label, value: compactTraceValue(value) });
  };
  if (semanticStage === "routing") {
    add("链路", routeText(details.route));
    add("技能", details.skill_name);
    add("原因", details.route_reason);
    add("意图", details.intent);
  } else if (semanticStage === "planning") {
    add("意图", details.intent || details.skill_name);
    add("风险", details.risk_level);
    add("步骤", Array.isArray(details.steps) ? `${details.steps.length} 步` : null);
    add("缺参", details.planner_missing_info?.length ? details.planner_missing_info.join("、") : "无");
  } else if (semanticStage === "tool") {
    add("工具", details.tool);
    add("技能", details.skill_name);
    add("耗时", details.duration_ms != null ? `${details.duration_ms}ms` : null);
    add("输出", details.output_summary);
    add("错误", details.error_message);
  } else if (semanticStage === "prompt") {
    add("模型", details.model_id);
    add("Instructions", details.instruction_count != null ? `${details.instruction_count} 段` : null);
    add("工具", details.tool_count != null ? `${details.tool_count} 个` : null);
    add("技能", details.skill_count != null ? `${details.skill_count} 个` : null);
  } else if (semanticStage === "final") {
    add("停止原因", details.stop_reason);
    add("回复", details.response_preview);
    add("工具数", details.observation_count != null ? `${details.observation_count} 个` : null);
  }
  return insights.slice(0, 4);
}
function traceSemanticStage(stage) {
  const mapping = {
    prompt_ready: "prompt",
    prompt: "prompt",
    memory: "memory",
    loop_started: "routing",
    routing: "routing",
    planning: "planning",
    reasoning: "reasoning",
    iteration_started: "iteration",
    iteration_observed: "iteration",
    iteration_evaluated: "iteration",
    tool_started: "tool",
    tool_finished: "tool",
    tool: "tool",
    waiting_input: "input",
    waiting_confirmation: "input",
    input: "input",
    final_response: "final",
    final: "final",
    error: "final",
  };
  return mapping[stage] || stage || "event";
}
function traceStageText(stage) {
  const mapping = {
    prompt_ready: "Prompt",
    prompt: "Prompt",
    memory: "记忆",
    loop_started: "启动",
    routing: "路由",
    planning: "规划",
    reasoning: "思考",
    tool_started: "工具开始",
    tool_finished: "工具结果",
    tool: "工具",
    waiting_input: "补参",
    waiting_confirmation: "确认",
    input: "补参",
    final_response: "回复",
    final: "回复",
    error: "错误",
  };
  return mapping[stage] || stage || "事件";
}
function traceStatusText(status) {
  const mapping = { success: "成功", needs_input: "待补充", needs_confirmation: "待确认", error: "失败", running: "执行中", idle: "空闲" };
  return mapping[status] || status || "未知";
}
function routeText(route) {
  if (route === "skill" || route === "skill_command") return "技能";
  if (route === "agent_loop" || route === "react") return "Agent loop";
  if (route === "local") return "本地";
  if (route === "error") return "错误";
  if (route === "planner") return "规划";
  return route;
}
function traceDetailLabel(key) {
  const mapping = {
    route: "执行链路",
    execution_source: "执行来源",
    error_stage: "错误阶段",
    intent: "识别意图",
    risk_level: "风险等级",
    steps: "步骤",
    planner_missing_info: "规划缺参",
    missing_info: "缺失信息",
    stop_reason: "停止原因",
    response_preview: "回复摘要",
    model_id: "模型",
    context_token_budget: "上下文预算",
    instruction_count: "Instructions 数",
    tool_count: "工具数",
    skill_count: "技能数",
    skill_name: "技能",
    tool_name: "工具",
    route_reason: "命中原因",
    tool: "工具",
    inputs: "入参",
    duration_ms: "耗时",
    attempts: "尝试次数",
    output_summary: "输出摘要",
    error_message: "错误信息",
    observation_count: "工具步骤数",
    reasoning_content: "思考内容",
    character_count: "思考字符数",
  };
  return mapping[key] || key;
}
function formatTraceValue(value) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}
