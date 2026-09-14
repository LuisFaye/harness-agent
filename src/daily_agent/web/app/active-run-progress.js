import { buildProcessTimeline, processSignature } from "./process-view.js";

const CAPABILITY_LABELS = {
  task: "任务",
  reasoning: "思考",
  skill: "Skill",
  tool: "Tool",
  mcp: "MCP",
  subagent: "Sub-Agent",
  interaction: "交互",
};

const STATUS_LABELS = {
  pending: "等待中",
  running: "进行中",
  success: "已完成",
  error: "失败",
  blocked: "受阻",
  cancelled: "已取消",
  cancel_requested: "正在停止",
  needs_input: "等待补充",
  needs_confirmation: "等待确认",
};

export function renderActiveRunProgress(run) {
  const items = Array.isArray(run?.activityItems) ? run.activityItems : [];
  const timeline = buildProcessTimeline(items, run?.status || "running", false);
  const steps = flattenTimeline(timeline);
  const current = [...steps].reverse().find((step) => step.status === "running")
    || steps[steps.length - 1];
  const currentAction = run?.cancelRequested
    ? "正在安全停止当前任务..."
    : current?.action || "正在理解你的需求...";
  const signature = processSignature(items, run?.status || "running");
  return `
    <section class="active-run-progress" data-progress-signature="${escapeHtml(signature)}"
             aria-live="polite" aria-label="Agent 实时执行过程">
      <div class="active-thinking">
        <span class="active-thinking-logo" aria-hidden="true">SB</span>
        <span class="active-thinking-copy">
          <strong>${run?.cancelRequested ? "正在停止" : "正在思考..."}</strong>
          <span>${escapeHtml(currentAction)}</span>
        </span>
        <span class="active-thinking-count">${steps.length} 个步骤</span>
      </div>
      <ol class="active-step-list" aria-label="实时执行步骤">
        ${steps.map(renderStep).join("")}
      </ol>
      <div class="active-run-actions">
        <button type="button" data-active-run-action="details">查看详情</button>
        <button type="button" class="danger" data-active-run-action="stop"
                ${run?.cancelRequested ? "disabled" : ""}>
          ${run?.cancelRequested ? "正在停止" : "停止"}
        </button>
      </div>
    </section>
  `;
}

function flattenTimeline(nodes, depth = 0) {
  return (Array.isArray(nodes) ? nodes : []).flatMap((node) => [
    { ...node, depth },
    ...flattenTimeline(node.children, depth + 1),
  ]);
}

function renderStep(step) {
  const type = step.capabilityType || "task";
  const status = step.status || "pending";
  return `
    <li class="active-step status-${escapeHtml(status)} ${step.depth ? "is-child" : ""}">
      <span class="active-step-marker" aria-hidden="true"></span>
      <span class="active-step-type type-${escapeHtml(type)}">${escapeHtml(CAPABILITY_LABELS[type] || "步骤")}</span>
      <span class="active-step-action">${escapeHtml(step.action || "处理当前步骤")}</span>
      <span class="active-step-status">${escapeHtml(STATUS_LABELS[status] || "处理中")}</span>
    </li>
  `;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
