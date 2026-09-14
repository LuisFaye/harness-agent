import { elements } from "./dom.js";
import { state } from "./state.js";

export function initializeWorkingState(query) {
  updateWorkingState({
    goal_summary: String(query || "").trim(),
    phase: "preparing",
    current_action: "正在准备任务",
    completed_steps: [],
    evidence_summaries: [],
    blocker: null,
    next_action: "理解目标并选择下一步",
    budget: { tool_calls: 0, tool_call_limit: 8, elapsed_seconds: 0 },
  });
}

export function updateWorkingState(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return;
  state.latestWorkingState = snapshot;
  renderWorkingStatus();
}

export function renderWorkingStatus() {
  const snapshot = state.latestWorkingState;
  if (!snapshot) {
    resetWorkingStatus();
    return;
  }
  const budget = snapshot.budget || {};
  const completed = Array.isArray(snapshot.completed_steps) ? snapshot.completed_steps : [];
  const evidence = Array.isArray(snapshot.evidence_summaries) ? snapshot.evidence_summaries : [];
  const savedTokens = Math.max(0, Number(budget.saved_estimated_tokens || 0));
  elements.workingStatusGoal.textContent = snapshot.goal_summary || "未记录";
  elements.workingStatusCompleted.textContent = completed.length ? completed.join("；") : "暂无";
  elements.workingStatusEvidence.textContent = evidence.length
    ? evidence.map((item) => `${item.tool || "结果"}：${item.summary || "已记录"}`).join("；")
    : "暂无";
  elements.workingStatusBlocker.textContent = snapshot.blocker || "无";
  elements.workingStatusNext.textContent = snapshot.next_action || "无";
  elements.workingStatusTokens.textContent = savedTokens
    ? `估算已节省 ${savedTokens} Token`
    : "尚未产生压缩数据";
}

export function resetWorkingStatus() {
  state.latestWorkingState = null;
  elements.workingStatusGoal.textContent = "未记录";
  elements.workingStatusCompleted.textContent = "暂无";
  elements.workingStatusEvidence.textContent = "暂无";
  elements.workingStatusBlocker.textContent = "无";
  elements.workingStatusNext.textContent = "等待任务";
  elements.workingStatusTokens.textContent = "尚未产生压缩数据";
}
