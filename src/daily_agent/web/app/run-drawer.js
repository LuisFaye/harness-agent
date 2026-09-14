import { elements } from "./dom.js";
import { renderLoadedProcessPanel, renderProcessPanel } from "./process-view.js";
import { state } from "./state.js";
import { escapeHtml } from "./utils.js";

let returnFocus = null;

export function openLiveRunDrawer(run) {
  if (!run) return;
  state.runDrawerRunId = run.runId || run.id || "active";
  openDrawer();
  elements.runDrawerStatus.textContent = statusText(run.status);
  elements.runDrawerTitle.textContent = "当前运行";
  elements.runDrawerMeta.textContent = `${run.activityItems?.length || 0} 个步骤 · 实时更新`;
  elements.runDrawerProcess.innerHTML = renderProcessPanel({
    items: run.activityItems || [],
    status: run.status || "running",
    durationMs: Math.max(0, Date.now() - run.startedAt),
    runId: run.runId || "",
    loaded: true,
    completed: false,
    forceOpen: true,
    live: true,
  });
}

export function refreshLiveRunDrawer(run) {
  const identities = new Set([run?.id, run?.runId, "active"].filter(Boolean));
  if (!run || elements.runDrawerBackdrop.hidden || !identities.has(state.runDrawerRunId)) {
    return;
  }
  openLiveRunDrawer(run);
}

export function openPersistedRunDrawer(runId) {
  state.runDrawerRunId = runId;
  openDrawer();
  elements.runDrawerStatus.textContent = "读取中";
  elements.runDrawerTitle.textContent = "本次运行";
  elements.runDrawerMeta.textContent = runId;
  elements.runDrawerProcess.innerHTML = `
    <div class="process-loading" role="status">
      <span aria-hidden="true"></span><strong>正在读取执行过程...</strong>
    </div>
  `;
}

export function renderPersistedRunDrawer(detail) {
  if (!detail || state.runDrawerRunId !== detail.run_id) return;
  elements.runDrawerStatus.textContent = statusText(detail.status);
  elements.runDrawerTitle.textContent = detail.query_summary || "本次运行";
  elements.runDrawerMeta.textContent = [
    detail.model_id,
    formatDuration(detail.duration_ms),
    detail.run_id,
  ].filter(Boolean).join(" · ");
  renderWorkingSnapshot(detail.working_state || null);
  elements.runDrawerProcess.innerHTML = renderLoadedProcessPanel(detail, { forceOpen: true });
}

export function renderRunDrawerError(runId, message) {
  if (state.runDrawerRunId !== runId) return;
  elements.runDrawerStatus.textContent = "读取失败";
  elements.runDrawerProcess.innerHTML = `
    <div class="process-error" role="alert">
      <strong>无法读取运行详情</strong>
      <p>${escapeHtml(message || "请稍后重试。")}</p>
    </div>
  `;
}

export function closeRunDrawer() {
  elements.runDrawerBackdrop.hidden = true;
  document.body.classList.remove("run-drawer-open");
  state.runDrawerRunId = "";
  if (returnFocus?.isConnected) returnFocus.focus();
  returnFocus = null;
}

export function isRunDrawerOpen() {
  return !elements.runDrawerBackdrop.hidden;
}

function openDrawer() {
  if (elements.runDrawerBackdrop.hidden) {
    returnFocus = document.activeElement;
  }
  elements.runDrawerBackdrop.hidden = false;
  document.body.classList.add("run-drawer-open");
  window.requestAnimationFrame(() => elements.runDrawer.focus({ preventScroll: true }));
}

function statusText(status) {
  return ({
    running: "执行中",
    success: "已完成",
    error: "执行失败",
    cancelled: "已取消",
    cancel_requested: "正在停止",
    needs_input: "等待补充",
    needs_confirmation: "等待确认",
  })[status] || "执行过程";
}

function formatDuration(value) {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "";
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;
  return `${(milliseconds / 1000).toFixed(milliseconds < 10000 ? 1 : 0)}s`;
}

function renderWorkingSnapshot(snapshot) {
  const fields = [
    [elements.workingStatusGoal, snapshot?.goal_summary || "这次运行未保存目标摘要"],
    [elements.workingStatusCompleted, listText(snapshot?.completed_steps)],
    [elements.workingStatusEvidence, evidenceText(snapshot?.evidence_summaries)],
    [elements.workingStatusBlocker, snapshot?.blocker || "无"],
    [elements.workingStatusNext, snapshot?.next_action || "已结束"],
    [
      elements.workingStatusTokens,
      Number(snapshot?.budget?.saved_estimated_tokens || 0)
        ? `估算已节省 ${Number(snapshot.budget.saved_estimated_tokens)} Token`
        : "无压缩记录",
    ],
  ];
  fields.forEach(([element, value]) => {
    if (element) element.textContent = value;
  });
}

function listText(items) {
  return Array.isArray(items) && items.length ? items.join("；") : "暂无";
}

function evidenceText(items) {
  if (!Array.isArray(items) || !items.length) return "暂无";
  return items.map((item) => `${item.tool || "结果"}：${item.summary || "已记录"}`).join("；");
}
