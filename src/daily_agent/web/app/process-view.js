const STATUS_TEXT = {
  running: "正在处理",
  success: "已完成",
  error: "执行失败",
  cancelled: "已取消",
  cancel_requested: "正在停止",
  needs_input: "等待补充信息",
  needs_confirmation: "等待操作确认",
  history: "执行过程",
};

const TERMINAL_STATUSES = new Set(["success", "error", "cancelled", "needs_input", "needs_confirmation"]);
const SECRET_KEY_PATTERN = /(authorization|token|api[_-]?key|password|secret|cookie)/i;

export function processItemsFromTrace(trace) {
  return (Array.isArray(trace) ? trace : []).map((item, index) => ({
    key: item.key || `${item.stage || "step"}-${index}`,
    status: item.status || "success",
    stage: item.stage || "step",
    text: item.summary || item.title || "执行步骤已完成。",
    meta: item.title && item.title !== item.summary ? item.title : processStageText(item.stage),
    title: item.title || processStageText(item.stage),
    details: item.details && typeof item.details === "object" ? item.details : {},
  }));
}

export function processSignature(items, status = "running") {
  const source = `${status}:${(Array.isArray(items) ? items : []).map((item) => [
    item.key,
    item.status,
    item.stage,
    item.text,
    item.meta,
    compactJson(item.details || {}),
  ].join("~")).join("|")}`;
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

export function renderProcessPanel({
  items = [],
  status = "running",
  durationMs = null,
  runId = "",
  loaded = true,
  completed = false,
  forceOpen = null,
  live = false,
  interactive = true,
} = {}) {
  const normalizedItems = Array.isArray(items) ? items : [];
  const timeline = buildProcessTimeline(normalizedItems, status, completed);
  const stepCount = countTimelineSteps(timeline);
  const shouldOpen = forceOpen == null
    ? ["error", "needs_input", "needs_confirmation"].includes(status)
    : Boolean(forceOpen);
  const summary = processSummary(normalizedItems, status, durationMs, stepCount, loaded);
  const attributes = [
    shouldOpen ? "open" : "",
    `class="message-process execution-stream status-${escapeHtml(status)}"`,
    runId ? `data-process-run-id="${escapeHtml(runId)}"` : "",
    `data-process-loaded="${loaded ? "true" : "false"}"`,
    `data-process-signature="${processSignature(normalizedItems, status)}"`,
    live ? "data-process-live=\"true\"" : "",
  ].filter(Boolean).join(" ");
  return `
    <details ${attributes}>
      <summary class="process-overview-summary">
        <span class="process-summary-title">
          <span class="process-status-icon" aria-hidden="true">${statusIcon(status)}</span>
          <strong>${escapeHtml(summary.title)}</strong>
        </span>
        <span class="process-summary-side">
          <span class="process-summary-meta">${escapeHtml(summary.meta)}</span>
          ${chevronIcon("process-summary-chevron")}
        </span>
      </summary>
      <div class="message-process-body" ${live ? "aria-live=\"polite\"" : ""}>
        ${loaded ? renderProcessBody(timeline, status, runId, interactive) : renderLoading()}
      </div>
    </details>
  `;
}

function processSummary(items, status, durationMs, stepCount, loaded) {
  if (!loaded) return { title: "执行过程", meta: "展开后加载" };
  if (status === "running") {
    return {
      title: currentActionText(items),
      meta: stepCount ? `已记录 ${stepCount} 个步骤` : "",
    };
  }
  const duration = durationMs == null ? "" : `处理了 ${formatDuration(durationMs)}`;
  const steps = `执行了 ${stepCount} 个步骤`;
  return {
    title: STATUS_TEXT[status] || "执行过程",
    meta: [duration, steps].filter(Boolean).join(" · "),
  };
}

function currentActionText(items) {
  const current = [...items].reverse().find((item) => item.status === "running") || items[items.length - 1];
  if (!current) return "正在分析需求…";
  const kind = processStageKind(current.stage);
  if (kind === "tool") {
    const skillName = current.details?.skill_name;
    if (skillName) return `正在执行「${skillName}」Skill…`;
    if (isMcpTool(current)) return `正在通过 MCP 执行「${toolName(current)}」…`;
    return `正在执行「${toolName(current) || "Tool"}」…`;
  }
  if (kind === "response") return "正在生成回复…";
  if (kind === "plan" || kind === "iteration") return "正在规划下一步…";
  if (kind === "reasoning") return "Agent 正在思考…";
  if (kind === "subagent") return `Sub-Agent「${current.details?.agent_name || current.title || "子任务"}」正在执行…`;
  if (kind === "input") return current.status === "needs_confirmation" ? "等待操作确认…" : "等待补充信息…";
  return "正在分析需求…";
}

export function buildProcessTimeline(items, status, completed) {
  const toolItems = mergeToolItems(items.filter((item) => processStageKind(item.stage) === "tool"));
  const routeItem = items.find((item) => processStageKind(item.stage) === "route");
  const planItem = items.find((item) => processStageKind(item.stage) === "plan");
  const finalItem = [...items].reverse().find((item) => processStageKind(item.stage) === "response");
  const iterationItems = items.filter((item) => processStageKind(item.stage) === "iteration");
  const reasoningItem = [...items].reverse().find((item) => processStageKind(item.stage) === "reasoning");
  const inputItems = items.filter((item) => processStageKind(item.stage) === "input");
  const subAgentItems = items.filter((item) => processStageKind(item.stage) === "subagent");
  const terminal = completed || TERMINAL_STATUSES.has(status) || Boolean(finalItem && finalItem.status !== "running");
  const timeline = [];

  if (routeItem) {
    timeline.push(taskNode({
      key: routeItem.key || "task-routing",
      status: routeItem.status || "success",
      action: "判断处理方式",
      detail: routeItem.text,
    }));
  } else if (!terminal && !planItem && !toolItems.length && !iterationItems.length) {
    timeline.push(taskNode({
      key: "task-understanding",
      status: "running",
      action: "正在理解需求",
      detail: "正在识别任务目标和限制条件。",
    }));
  }

  if (planItem && !sameProcessText(planItem.text, routeItem?.text)) {
    timeline.push(taskNode({
      key: planItem.key || "task-planning",
      status: planItem.status || "success",
      action: planItem.status === "running" ? "正在制定执行计划" : "制定执行计划",
      detail: planItem.text,
      capabilityName: "任务计划",
    }));
  }

  if (reasoningItem) timeline.push(reasoningNode(reasoningItem));
  timeline.push(...buildCapabilityNodes(toolItems));
  if (!toolItems.length) timeline.push(...buildIterationNodes(iterationItems));
  timeline.push(...subAgentItems.map(subAgentNode));
  timeline.push(...inputItems.map(inputNode));

  if (!inputItems.length || finalItem) {
    const hasFinishedTool = toolItems.some((item) => item.status !== "running");
    const responseStatus = finalItem?.status || (terminal ? status : hasFinishedTool ? "running" : "pending");
    timeline.push({
      key: finalItem?.key || "task-response",
      status: responseStatus,
      action: responseAction(responseStatus),
      capabilityType: "task",
      capabilityName: "结果整理",
      durationMs: finalItem?.details?.duration_ms,
      details: {
        output_summary: finalItem?.text || taskResponseDetail(toolItems, responseStatus),
        stop_reason: finalItem?.details?.stop_reason,
      },
    });
  }
  return timeline;
}

function sameProcessText(left, right) {
  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const normalizedLeft = normalize(left);
  const normalizedRight = normalize(right);
  return Boolean(normalizedLeft && normalizedRight && normalizedLeft === normalizedRight);
}

function reasoningNode(item) {
  return {
    key: item.key || "reasoning",
    status: item.status || "running",
    action: item.status === "running" ? "Agent 正在思考" : "Agent 已完成思考",
    capabilityType: "reasoning",
    capabilityName: "思考",
    details: {
      output_summary: item.details?.reasoning_content || item.text,
    },
  };
}

function buildIterationNodes(items) {
  const groups = new Map();
  items.forEach((item) => {
    const iteration = Number(item.details?.iteration || 0);
    if (!iteration) return;
    if (!groups.has(iteration)) groups.set(iteration, []);
    groups.get(iteration).push(item);
  });
  return [...groups.entries()].sort(([left], [right]) => left - right).map(([iteration, events]) => {
    const started = events.find((item) => item.stage === "iteration_started");
    const observed = events.find((item) => item.stage === "iteration_observed");
    const evaluated = events.find((item) => item.stage === "iteration_evaluated");
    const tool = started?.details?.tool || observed?.details?.tool || "Tool";
    const status = observed?.status === "blocked" ? "error" : evaluated ? "success" : "running";
    return {
      key: `iteration-${iteration}`,
      status,
      action: `第 ${iteration} 轮 · 调用 Tool「${tool}」`,
      capabilityType: isMcpTool(started || observed || {}) ? "mcp" : "tool",
      capabilityName: isMcpTool(started || observed || {}) ? mcpSourceName(started || observed || {}) : "Tool",
      details: {
        output_summary: observed?.text || started?.text || "正在等待 Tool 返回结果。",
        logs: evaluated?.text ? `结果验证：${evaluated.text}` : "",
      },
    };
  });
}

function taskNode({ key, status, action, detail, capabilityName = "任务" }) {
  return {
    key,
    status,
    action,
    capabilityType: "task",
    capabilityName,
    details: { output_summary: detail },
  };
}

function buildCapabilityNodes(items) {
  const nodes = [];
  const skillGroups = new Map();
  items.forEach((item, index) => {
    const skillName = String(item.details?.skill_name || "").trim();
    if (!skillName) {
      nodes.push(toolNode(item, index));
      return;
    }
    if (!skillGroups.has(skillName)) {
      const group = {
        key: `skill-${skillName}`,
        status: "pending",
        action: `执行「${skillName}」`,
        capabilityType: "skill",
        capabilityName: "Skill",
        durationMs: null,
        details: {},
        children: [],
      };
      skillGroups.set(skillName, group);
      nodes.push(group);
    }
    const group = skillGroups.get(skillName);
    group.children.push(toolNode(item, index));
    if (item.details?.duration_ms != null) {
      group.durationMs = Number(group.durationMs || 0) + Number(item.details.duration_ms);
    }
    group.status = aggregateStatus(group.children.map((child) => child.status));
    group.details.output_summary = `${group.children.length} 个 Tool 组成这项复合能力。`;
  });
  return nodes;
}

function toolNode(item, index) {
  const details = item.details || {};
  const name = toolName(item) || `Tool ${index + 1}`;
  const mcp = isMcpTool(item);
  return {
    key: item.key || `tool-${index + 1}`,
    status: item.status || "success",
    action: `调用 Tool「${name}」`,
    capabilityType: mcp ? "mcp" : "tool",
    capabilityName: mcp ? mcpSourceName(item) : "Tool",
    durationMs: details.duration_ms,
    details: {
      inputs: details.inputs,
      output_summary: details.output_summary || item.text,
      logs: details.logs || details.log_summary,
      error_message: details.error_message,
      attempts: details.attempts,
      source_summary: mcp ? `这个 Tool 由 ${mcpSourceName(item)} MCP 接入。` : "这是一次原子 Tool 操作。",
      artifacts: details.artifacts,
    },
    card: item.status === "error" || item.status === "cancelled",
  };
}

function subAgentNode(item, index) {
  const name = item.details?.agent_name || item.title || `Sub-Agent ${index + 1}`;
  return {
    key: item.key || `sub-agent-${index + 1}`,
    status: item.status || "running",
    action: `Sub-Agent「${name}」执行独立子任务`,
    capabilityType: "subagent",
    capabilityName: "Sub-Agent",
    durationMs: item.details?.duration_ms,
    details: {
      input_summary: item.details?.input_summary,
      output_summary: item.details?.output_summary || item.text,
      logs: item.details?.logs,
      error_message: item.details?.error_message,
      attempts: item.details?.attempts,
    },
    card: true,
  };
}

function inputNode(item, index) {
  const confirmation = item.status === "needs_confirmation" || String(item.stage).includes("confirmation");
  return {
    key: item.key || `input-${index + 1}`,
    status: confirmation ? "needs_confirmation" : "needs_input",
    action: confirmation ? "等待用户确认高风险操作" : "等待用户补充信息",
    capabilityType: "interaction",
    capabilityName: confirmation ? "操作确认" : "用户输入",
    details: {
      output_summary: item.text,
      missing_info: item.details?.missing_info,
      stop_reason: item.details?.stop_reason,
    },
    card: true,
  };
}

function mergeToolItems(items) {
  const merged = [];
  const indexes = new Map();
  items.forEach((item, index) => {
    const details = item.details || {};
    const identity = details.iteration
      ? `iteration:${details.iteration}:${toolName(item)}`
      : String(item.key || `tool:${index}`).replace(/-(started|finished)$/, "");
    const existingIndex = indexes.get(identity);
    if (existingIndex == null) {
      indexes.set(identity, merged.length);
      merged.push(item);
      return;
    }
    const previous = merged[existingIndex];
    merged[existingIndex] = {
      ...previous,
      ...item,
      key: previous.key || item.key,
      details: { ...(previous.details || {}), ...(item.details || {}) },
    };
  });
  return merged;
}

function renderProcessBody(timeline, status, runId = "", interactive = true) {
  if (!timeline.length) return `<div class="process-empty">${escapeHtml(status === "running" ? "正在分析需求…" : "没有可展示的执行步骤。")}</div>`;
  return `
    <ol class="execution-tree">
      ${timeline.map((node) => renderTimelineNode(node)).join("")}
    </ol>
    ${interactive ? renderConfirmationActions(status, runId) : ""}
  `;
}

export function renderConfirmationActions(status, runId = "") {
  if (status !== "needs_confirmation" || !runId) return "";
  return `
    <div class="confirmation-actions" role="group" aria-label="高风险操作确认">
      <p>确认后 Agent 会继续执行刚才暂停的操作。</p>
      <div>
        <button type="button" class="confirmation-button primary" data-confirmation-action="confirm" data-confirmation-run-id="${escapeHtml(runId)}">确认执行</button>
        <button type="button" class="confirmation-button secondary" data-confirmation-action="cancel" data-confirmation-run-id="${escapeHtml(runId)}">取消</button>
      </div>
    </div>
  `;
}

function renderTimelineNode(node, nested = false) {
  const children = Array.isArray(node.children) ? node.children : [];
  const classNames = [
    "execution-node",
    `status-${node.status || "success"}`,
    `kind-${node.capabilityType || "task"}`,
    nested ? "is-child" : "",
    node.card ? "is-important" : "",
  ].filter(Boolean).join(" ");
  return `
    <li class="${classNames}">
      ${node.card ? renderImportantNode(node) : renderStepDetails(node)}
      ${children.length ? `<ol class="execution-children">${children.map((child) => renderTimelineNode(child, true)).join("")}</ol>` : ""}
    </li>
  `;
}

function renderStepDetails(node) {
  const details = renderDetailContent(node);
  return `
    <details class="process-timeline-step" data-step-key="${escapeHtml(node.key || "step")}">
      <summary class="execution-step-row">
        <span class="execution-step-status" aria-hidden="true">${statusIcon(node.status)}</span>
        <span class="execution-step-action">${escapeHtml(node.action || "执行步骤")}</span>
        <span class="capability-badge type-${escapeHtml(node.capabilityType || "task")}">${escapeHtml(capabilityBadgeText(node))}</span>
        <time>${node.durationMs == null ? "" : escapeHtml(formatDuration(node.durationMs))}</time>
        ${chevronIcon("execution-step-chevron")}
      </summary>
      <div class="execution-step-detail">${details}</div>
    </details>
  `;
}

function renderImportantNode(node) {
  return `
    <details class="process-timeline-step important-execution-card" data-step-key="${escapeHtml(node.key || "step")}" open>
      <summary class="execution-step-row">
        <span class="execution-step-status" aria-hidden="true">${statusIcon(node.status)}</span>
        <span class="execution-step-action">${escapeHtml(node.action || "需要处理")}</span>
        <span class="capability-badge type-${escapeHtml(node.capabilityType || "interaction")}">${escapeHtml(capabilityBadgeText(node))}</span>
        <time>${node.durationMs == null ? "" : escapeHtml(formatDuration(node.durationMs))}</time>
        ${chevronIcon("execution-step-chevron")}
      </summary>
      <div class="execution-step-detail">${renderDetailContent(node)}</div>
    </details>
  `;
}

function renderDetailContent(node) {
  const details = node.details || {};
  const fields = [];
  addDetailField(fields, "输入参数", details.inputs == null ? details.input_summary : summarizeInputs(details.inputs));
  addDetailField(fields, "输出摘要", details.output_summary);
  addDetailField(fields, "接入说明", details.source_summary);
  addDetailField(fields, "日志", formatValue(details.logs));
  addDetailField(fields, "错误", details.error_message, "error");
  if (details.attempts != null) {
    const retryCount = Math.max(0, Number(details.attempts || 0) - 1);
    addDetailField(fields, "重试次数", retryCount ? `${retryCount} 次` : "未重试");
  }
  if (Array.isArray(details.missing_info) && details.missing_info.length) {
    addDetailField(fields, "缺少信息", details.missing_info.join("、"));
  }
  addDetailField(fields, "停止原因", details.stop_reason);
  const artifacts = renderArtifacts(details.artifacts);
  if (!fields.length && !artifacts) addDetailField(fields, "步骤摘要", "这个步骤没有额外详情。可继续查看后续执行状态。");
  return `${fields.join("")}${artifacts}`;
}

function addDetailField(fields, label, value, kind = "") {
  if (value == null || value === "" || value === "未填写") return;
  fields.push(`
    <div class="execution-detail-field ${kind ? `field-${kind}` : ""}">
      <span>${escapeHtml(label)}</span>
      <p>${escapeHtml(compactText(value, label === "日志" ? 600 : 360))}</p>
    </div>
  `);
}

function renderArtifacts(artifacts) {
  if (!Array.isArray(artifacts) || !artifacts.length) return "";
  return `
    <div class="execution-artifacts">
      ${artifacts.map((item) => `
        <article class="execution-artifact-card">
          <strong>${escapeHtml(item?.name || item?.title || "执行结果")}</strong>
          <span>${escapeHtml(item?.type || "文件")}</span>
          ${item?.summary ? `<p>${escapeHtml(compactText(item.summary, 240))}</p>` : ""}
        </article>
      `).join("")}
    </div>
  `;
}

export function renderHistoricalProcessPanel(runId) {
  return renderProcessPanel({ status: "history", runId, loaded: false, completed: true });
}

export function renderLoadedProcessPanel(detail, { forceOpen = true } = {}) {
  return renderProcessPanel({
    items: processItemsFromTrace(detail?.trace),
    status: detail?.status || "success",
    durationMs: detail?.duration_ms,
    runId: detail?.run_id || "",
    loaded: true,
    completed: true,
    forceOpen,
    interactive: false,
  });
}

function countTimelineSteps(nodes) {
  return nodes.reduce((count, node) => count + 1 + countTimelineSteps(node.children || []), 0);
}

function aggregateStatus(statuses) {
  if (statuses.some((status) => status === "error" || status === "cancelled")) return "error";
  if (statuses.some((status) => status === "running")) return "running";
  if (statuses.some((status) => status === "pending")) return "pending";
  return "success";
}

function responseAction(status) {
  if (status === "running") return "正在生成回复";
  if (status === "pending") return "等待生成回复";
  if (status === "error") return "生成回复失败";
  if (status === "cancelled") return "回复生成已取消";
  return "生成最终回复";
}

function taskResponseDetail(toolItems, status) {
  if (status === "running") return "工具结果已经返回，正在整理结果摘要。";
  if (status === "pending") return toolItems.length ? "等待前面的 Tool 返回结果。" : "等待需求分析和计划完成。";
  if (status === "error") return "执行过程中发生错误，未能正常生成回复。";
  return toolItems.length ? `已根据 ${toolItems.length} 个 Tool 结果生成回复。` : "已直接根据现有信息生成回复。";
}

function capabilityBadgeText(node) {
  const type = {
    task: "任务",
    skill: "Skill",
    tool: "Tool",
    mcp: "MCP",
    reasoning: "思考",
    subagent: "Sub-Agent",
    interaction: "交互",
  }[node.capabilityType] || "步骤";
  const name = String(node.capabilityName || "").trim();
  return name && name !== type ? `${type} · ${name}` : type;
}

function isMcpTool(item) {
  const details = item?.details || {};
  const name = String(details.tool || item?.title || "");
  return details.source_type === "mcp" || Boolean(details.mcp_server_id || details.mcp_server_name) || /^mcp[_:]/i.test(name);
}

function mcpSourceName(item) {
  const details = item?.details || {};
  if (details.mcp_server_name) return String(details.mcp_server_name);
  if (details.mcp_server_id) return String(details.mcp_server_id);
  const match = String(details.tool || item?.title || "").match(/^mcp[_:]([^_:]+)/i);
  return match?.[1] || "外部服务";
}

function summarizeInputs(inputs) {
  if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) return compactText(formatValue(inputs), 240);
  return Object.entries(inputs).slice(0, 6).map(([key, value]) => {
    const safeValue = SECRET_KEY_PATTERN.test(key) ? "***" : formatValue(value);
    return `${key}：${safeValue}`;
  }).join(" · ");
}

function formatValue(value) {
  if (value == null || value === "") return "未填写";
  if (Array.isArray(value)) return value.map((item) => formatValue(item)).join("、");
  if (typeof value === "object") return compactJson(value);
  return String(value);
}

function toolName(item) {
  return item?.details?.tool || item?.title || item?.meta || "";
}

function compactJson(value) {
  try {
    return JSON.stringify(value, (key, item) => SECRET_KEY_PATTERN.test(key) ? "***" : item);
  } catch {
    return String(value || "");
  }
}

function compactText(value, limit) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function processStageText(stage) {
  const value = String(stage || "");
  if (value.includes("sub_agent") || value.includes("subagent")) return "Sub-Agent";
  if (value.includes("iteration")) return "执行轮次";
  if (value.includes("reasoning")) return "深度思考";
  if (value.includes("prompt")) return "任务上下文";
  if (value.includes("memory")) return "记忆";
  if (value.includes("route") || value.includes("loop")) return "任务判断";
  if (value.includes("plan")) return "执行计划";
  if (value.includes("tool") || value.includes("skill")) return "Tool 调用";
  if (value.includes("waiting") || value.includes("input") || value.includes("confirmation")) return "等待交互";
  if (value.includes("final") || value.includes("response")) return "生成回复";
  if (value.includes("error")) return "错误处理";
  return "执行步骤";
}

function processStageKind(stage) {
  const value = String(stage || "");
  if (value.includes("sub_agent") || value.includes("subagent")) return "subagent";
  if (value.includes("iteration")) return "iteration";
  if (value.includes("reasoning")) return "reasoning";
  if (value.includes("tool") || value.includes("skill")) return "tool";
  if (value.includes("waiting") || value.includes("input") || value.includes("confirmation")) return "input";
  if (value.includes("memory")) return "memory";
  if (value.includes("plan")) return "plan";
  if (value.includes("prompt")) return "prompt";
  if (value.includes("route") || value.includes("loop")) return "route";
  if (value.includes("final") || value.includes("response") || value.includes("error")) return "response";
  return "step";
}

function statusIcon(status) {
  if (status === "running" || status === "cancel_requested") return '<span class="status-spinner"></span>';
  if (status === "error" || status === "cancelled") return '<svg viewBox="0 0 16 16"><path d="m5 5 6 6m0-6-6 6"/></svg>';
  if (status === "needs_input" || status === "needs_confirmation" || status === "blocked") return '<svg viewBox="0 0 16 16"><path d="M8 4v4m0 3h.01"/></svg>';
  if (status === "pending") return '<span class="status-pending-dot"></span>';
  return '<svg viewBox="0 0 16 16"><path d="m4 8 2.5 2.5L12 5"/></svg>';
}

function chevronIcon(className) {
  return `<svg class="${className}" viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4"/></svg>`;
}

function renderLoading() {
  return '<div class="process-loading"><span aria-hidden="true"></span><strong>正在读取执行过程...</strong></div>';
}

function formatDuration(value) {
  const duration = Math.max(0, Number(value || 0));
  if (duration < 1000) return `${Math.round(duration)} 毫秒`;
  const seconds = Math.round(duration / 100) / 10;
  return `${seconds} 秒`;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
