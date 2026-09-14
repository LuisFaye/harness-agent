import { request } from "./api.js";
import { elements } from "./dom.js";
import { runState, buildRunsQuery, updateWorkspaceLocation } from "./run-state.js";
import { state } from "./state.js";
import { escapeHtml, formatDateTime } from "./utils.js";

export async function loadAgentRuns({ selectRunId = "", resetOffset = false } = {}) {
  if (resetOffset) runState.offset = 0;
  runState.loadingList = true;
  renderRunListLoading();
  syncRunFilterState();
  const url = buildRunsQuery({
    userId: state.userId,
    status: elements.runFilterStatus.value,
    sessionId: elements.runFilterSession.value,
    keyword: elements.runFilterKeyword.value,
    limit: runState.limit,
    offset: runState.offset,
  });
  try {
    const payload = await request(url);
    runState.items = payload.items || [];
    runState.total = Number(payload.total || 0);
    runState.limit = Number(payload.limit || runState.limit);
    runState.offset = Number(payload.offset || 0);
    renderAgentRunList();
    const targetRunId = selectRunId || runState.selectedRunId || runState.items[0]?.run_id || "";
    if (targetRunId && (selectRunId || targetRunId !== runState.selectedRunId || !runState.detail)) {
      await openAgentRun(targetRunId, { updateLocation: Boolean(selectRunId), focus: false });
    } else if (!targetRunId) {
      renderRunDetailEmpty();
    }
  } catch (error) {
    renderRunListError(error.message);
  } finally {
    runState.loadingList = false;
    updateRunPagination();
  }
}

export async function openAgentRun(runId, { updateLocation = true, focus = true } = {}) {
  const normalizedRunId = String(runId || "").trim();
  if (!normalizedRunId) return;
  runState.selectedRunId = normalizedRunId;
  runState.loadingDetail = true;
  renderAgentRunList();
  renderRunDetailLoading(normalizedRunId);
  if (updateLocation) updateWorkspaceLocation("runs", normalizedRunId);
  try {
    runState.detail = await request(
      `/runs/${encodeURIComponent(normalizedRunId)}?user_id=${encodeURIComponent(state.userId)}`
    );
    renderAgentRunDetail(runState.detail);
    if (focus) elements.agentRunDetail.focus({ preventScroll: true });
  } catch (error) {
    runState.detail = null;
    renderRunDetailError(normalizedRunId, error.message);
  } finally {
    runState.loadingDetail = false;
  }
}

export async function applyRunFilters(event) {
  event?.preventDefault();
  runState.selectedRunId = "";
  runState.detail = null;
  await loadAgentRuns({ resetOffset: true });
}

export async function resetRunFilters() {
  elements.runFilterKeyword.value = "";
  elements.runFilterStatus.value = "";
  elements.runFilterSession.value = "";
  runState.selectedRunId = "";
  runState.detail = null;
  updateWorkspaceLocation("runs");
  await loadAgentRuns({ resetOffset: true });
}

export async function moveRunPage(direction) {
  const nextOffset = Math.max(0, runState.offset + direction * runState.limit);
  if (nextOffset === runState.offset || nextOffset >= runState.total) return;
  runState.offset = nextOffset;
  runState.selectedRunId = "";
  runState.detail = null;
  await loadAgentRuns();
}

export async function handleAgentRunListClick(event) {
  const button = event.target.closest("[data-agent-run-id]");
  if (!button) return;
  await openAgentRun(button.dataset.agentRunId || "");
}

function syncRunFilterState() {
  elements.runFilterStatus.value = elements.runFilterStatus.value || "";
  elements.runFilterSession.value = elements.runFilterSession.value.trim();
  elements.runFilterKeyword.value = elements.runFilterKeyword.value.trim();
}

function renderRunListLoading() {
  elements.agentRunCount.textContent = "正在读取运行记录";
  elements.agentRunList.innerHTML = Array.from({ length: 4 }, () => `
    <div class="run-list-skeleton" aria-hidden="true">
      <span></span><span></span><span></span>
    </div>
  `).join("");
}

function renderRunListError(message) {
  elements.agentRunCount.textContent = "运行记录暂时不可用";
  elements.agentRunList.innerHTML = `
    <div class="run-list-message error" role="alert">
      <strong>加载失败</strong>
      <p>${escapeHtml(message || "请检查 MySQL 连接后重试。")}</p>
    </div>
  `;
}

function renderAgentRunList() {
  elements.agentRunCount.textContent = runState.total
    ? `共 ${runState.total} 次运行 · 当前 ${runState.offset + 1}-${Math.min(runState.total, runState.offset + runState.items.length)}`
    : "暂无 Agent 运行";
  if (!runState.items.length) {
    elements.agentRunList.innerHTML = `
      <div class="run-list-message">
        <strong>暂无 Agent 运行</strong>
        <p>先去 Agent 页面发起一个任务，完成后会出现在这里。</p>
      </div>
    `;
    updateRunPagination();
    return;
  }
  elements.agentRunList.innerHTML = runState.items.map((item) => {
    const selected = item.run_id === runState.selectedRunId;
    return `
      <button type="button" class="agent-run-item ${selected ? "active" : ""}"
              data-agent-run-id="${escapeHtml(item.run_id)}" role="option" aria-selected="${selected}">
        <div class="agent-run-item-head">
          <span class="run-status ${escapeHtml(runStatusClass(item.status))}">
            <i aria-hidden="true"></i>${escapeHtml(runStatusText(item.status))}
          </span>
          <time>${escapeHtml(formatDateTime(item.finished_at || item.started_at))}</time>
        </div>
        <strong>${escapeHtml(item.query_summary || "未保存用户输入")}</strong>
        <code>${escapeHtml(item.run_id)}</code>
        <small>${escapeHtml(item.session_id || "unknown")} · ${escapeHtml(item.model_id || "模型未知")}</small>
      </button>
    `;
  }).join("");
  updateRunPagination();
}

function updateRunPagination() {
  elements.runPrevPage.disabled = runState.loadingList || runState.offset <= 0;
  elements.runNextPage.disabled = runState.loadingList || runState.offset + runState.limit >= runState.total;
}

function renderRunDetailLoading(runId) {
  elements.agentRunDetail.innerHTML = `
    <div class="run-detail-loading" role="status">
      <span></span>
      <strong>正在读取 ${escapeHtml(runId)}</strong>
    </div>
  `;
}

function renderRunDetailEmpty() {
  elements.agentRunDetail.innerHTML = `
    <div class="run-detail-empty">
      <strong>请选择一次运行</strong>
      <p>这里会展示回答、计划、Tool、Trace 和 Prompt。</p>
    </div>
  `;
}

function renderRunDetailError(runId, message) {
  elements.agentRunDetail.innerHTML = `
    <div class="run-detail-empty error" role="alert">
      <strong>运行详情加载失败</strong>
      <p>${escapeHtml(message || `没有找到 ${runId}`)}</p>
      <button type="button" class="ghost-button compact-button" data-retry-run-id="${escapeHtml(runId)}">重试</button>
    </div>
  `;
  elements.agentRunDetail.querySelector("[data-retry-run-id]")?.addEventListener("click", () => openAgentRun(runId));
}

function renderAgentRunDetail(detail) {
  const usage = detail.usage || {};
  const legacyNotice = detail.legacy_record
    ? `<div class="run-detail-notice">这是一条旧运行，部分详情当时没有保存。</div>`
    : "";
  const truncatedNotice = detail.truncated_fields?.length
    ? `<div class="run-detail-notice">为避免页面过大，${detail.truncated_fields.length} 个字段已截断。</div>`
    : "";
  elements.agentRunDetail.innerHTML = `
    <header class="run-detail-head">
      <div>
        <span class="run-status ${escapeHtml(runStatusClass(detail.status))}">
          <i aria-hidden="true"></i>${escapeHtml(runStatusText(detail.status))}
        </span>
        <h2>运行详情</h2>
        <code>${escapeHtml(detail.run_id)}</code>
      </div>
      <span class="run-detail-time">${escapeHtml(formatDateTime(detail.finished_at || detail.started_at))}</span>
    </header>
    ${legacyNotice}${truncatedNotice}
    <section class="run-overview" aria-label="运行概览">
      ${overviewItem("会话", detail.session_id || "unknown")}
      ${overviewItem("执行来源", executionSourceText(detail.execution_source))}
      ${overviewItem("模型", detail.model_id || "暂无数据")}
      ${overviewItem("耗时", formatDuration(detail.duration_ms))}
      ${overviewItem("输入 Token", formatToken(usage.input_tokens))}
      ${overviewItem("输出 Token", formatToken(usage.output_tokens))}
    </section>
    <section class="run-answer-section">
      <h3>用户输入</h3>
      <pre>${escapeHtml(detail.query || "当时未保存")}</pre>
      <h3>最终回答</h3>
      <pre class="run-answer-text">${escapeHtml(detail.response_text || "当时未保存完整回答")}</pre>
    </section>
    ${renderDetailGroup("执行计划", detail.plan, "这次运行没有保存执行计划。")}
    ${renderObservations(detail.observations || [])}
    ${renderTrace(detail.trace || [])}
    ${renderDetailGroup("Prompt 快照", detail.prompt_snapshot, "这次运行没有保存 Prompt 快照。")}
    ${renderError(detail)}
  `;
}

function overviewItem(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function renderDetailGroup(title, value, emptyText) {
  const hasValue = value && (typeof value !== "object" || Object.keys(value).length > 0);
  return `
    <details class="run-detail-group">
      <summary>${escapeHtml(title)}</summary>
      ${hasValue ? `<pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>` : `<p>${escapeHtml(emptyText)}</p>`}
    </details>
  `;
}

function renderObservations(items) {
  return `
    <details class="run-detail-group">
      <summary>Tool 调用 <span>${items.length}</span></summary>
      ${items.length ? `<div class="run-tool-list">${items.map((item, index) => `
        <article>
          <div><strong>${escapeHtml(item.tool || item.name || `Tool ${index + 1}`)}</strong><span>${escapeHtml(runStatusText(item.status))}</span></div>
          <small>${escapeHtml(formatDuration(item.duration_ms))}</small>
          <pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre>
        </article>
      `).join("")}</div>` : `<p>这次运行没有 Tool 调用。</p>`}
    </details>
  `;
}

function renderTrace(items) {
  return `
    <details class="run-detail-group">
      <summary>Trace 时间线 <span>${items.length}</span></summary>
      ${items.length ? `<div class="run-trace-list">${items.map((item, index) => `
        <article>
          <i aria-hidden="true">${index + 1}</i>
          <div>
            <strong>${escapeHtml(item.title || item.stage || "执行事件")}</strong>
            <p>${escapeHtml(item.summary || "")}</p>
            <small>${escapeHtml(runStatusText(item.status))} · ${escapeHtml(item.stage || "unknown")}</small>
          </div>
        </article>
      `).join("")}</div>` : `<p>这次运行没有保存 Trace。</p>`}
    </details>
  `;
}

function renderError(detail) {
  if (!detail.error_message && !detail.error_stage && !detail.stop_reason) return "";
  return `
    <details class="run-detail-group error-group" ${detail.status === "error" ? "open" : ""}>
      <summary>停止与错误信息</summary>
      <div class="run-error-content" role="${detail.status === "error" ? "alert" : "status"}">
        <p><strong>阶段：</strong>${escapeHtml(detail.error_stage || "未记录")}</p>
        <p><strong>原因：</strong>${escapeHtml(detail.stop_reason || "未记录")}</p>
        <pre>${escapeHtml(detail.error_message || "没有错误正文")}</pre>
      </div>
    </details>
  `;
}

function runStatusText(status) {
  const mapping = {
    queued: "排队中",
    running: "运行中",
    needs_input: "等待补充",
    needs_confirmation: "等待确认",
    success: "成功",
    error: "失败",
    max_iterations: "达到轮次上限",
    cancel_requested: "正在取消",
    cancelled: "已取消",
  };
  return mapping[status] || status || "未知";
}

function runStatusClass(status) {
  if (status === "success") return "success";
  if (["error", "max_iterations"].includes(status)) return "error";
  if (["needs_input", "needs_confirmation"].includes(status)) return "waiting";
  return "running";
}

function executionSourceText(value) {
  const mapping = {
    agent_loop: "Agent Loop",
    skill_command: "Skill 命令",
    local: "本地执行",
    error: "错误兜底",
  };
  return mapping[value] || value || "未知";
}

function formatDuration(value) {
  if (value == null || Number.isNaN(Number(value))) return "暂无数据";
  const milliseconds = Math.max(0, Number(value));
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  return `${(milliseconds / 1000).toFixed(milliseconds < 10000 ? 1 : 0)} 秒`;
}

function formatToken(value) {
  return value == null ? "暂无数据" : Number(value).toLocaleString("zh-CN");
}
