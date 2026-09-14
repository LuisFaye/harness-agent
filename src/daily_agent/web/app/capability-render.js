import { elements } from "./dom.js";
import { state } from "./state.js";
import { escapeHtml } from "./utils.js";

export function renderCapabilityCenter() {
  renderTools();
  renderSkillStates();
  renderMcpServers();
  switchCapabilityTab(state.capabilityTab);
}

export function switchCapabilityTab(tab) {
  state.capabilityTab = tab || "tools";
  elements.capabilityTabs.forEach((button) => button.classList.toggle("active", button.dataset.capabilityTab === state.capabilityTab));
  elements.capabilityPanels.forEach((panel) => panel.classList.toggle("active", panel.dataset.capabilityPanel === state.capabilityTab));
}

export function populateMcpForm(server) {
  state.selectedMcpServerId = server?.server_id || "";
  elements.mcpFormTitle.textContent = server ? `编辑 ${server.name}` : "新增 MCP Server";
  elements.mcpName.value = server?.name || "";
  elements.mcpTransport.value = server?.transport_type || "stdio";
  elements.mcpCommand.value = server?.command || "";
  elements.mcpArgs.value = (server?.args || []).join("\n");
  elements.mcpUrl.value = server?.url || "";
  elements.mcpEnvironmentKeys.value = (server?.environment_keys || []).join("\n");
  elements.mcpHeaderEnvMap.value = Object.entries(server?.header_env_map || {}).map(([key, value]) => `${key}=${value}`).join("\n");
}

export function readMcpForm() {
  return {
    name: elements.mcpName.value.trim(),
    transport_type: elements.mcpTransport.value,
    command: elements.mcpCommand.value.trim() || null,
    args: parseLines(elements.mcpArgs.value),
    url: elements.mcpUrl.value.trim() || null,
    environment_keys: parseLines(elements.mcpEnvironmentKeys.value),
    header_env_map: Object.fromEntries(
      parseLines(elements.mcpHeaderEnvMap.value)
        .map((line) => line.split("="))
        .filter((parts) => parts.length >= 2 && parts[0].trim() && parts.slice(1).join("=").trim())
        .map((parts) => [parts[0].trim(), parts.slice(1).join("=").trim()]),
    ),
    enabled: true,
  };
}

function renderTools() {
  elements.capabilityToolList.innerHTML = state.tools.length
    ? state.tools.map((item) => `
      <article class="capability-card">
        <div class="capability-card-head">
          <div class="capability-card-title"><span class="capability-type">Tool</span><h3>${escapeHtml(item.name)}</h3></div>
          ${statusPill(item.available ? "可调用" : item.enabled ? "暂不可用" : "已停用", item.available ? "ready" : item.enabled ? "warning" : "muted")}
        </div>
        <p class="capability-description">${escapeHtml(item.description || "暂无说明")}</p>
        <div class="capability-badges" aria-label="能力属性">
          <span class="capability-badge">${escapeHtml(item.source_type)} · ${escapeHtml(item.source_id)}</span>
          <span class="capability-badge">风险：${escapeHtml(riskLabel(item.risk_level))}</span>
          ${item.requires_confirmation ? statusPill("执行前确认", "warning", "capability-badge") : ""}
        </div>
        <p class="capability-reason ${item.available ? "is-ready" : item.enabled ? "is-warning" : ""}">${escapeHtml(item.unavailable_reason || "运行条件正常，可由 Agent 调用。")}</p>
        <div class="capability-actions"><button class="ghost-button compact-button" data-tool-action="${item.enabled ? "disable" : "enable"}" data-tool-id="${escapeHtml(item.tool_id)}">${item.enabled ? "停用 Tool" : "启用 Tool"}</button></div>
      </article>
    `).join("")
    : `<div class="run-info-empty">当前没有可注册 Tool。</div>`;
}

function renderSkillStates() {
  elements.capabilitySkillList.innerHTML = state.skills.length
    ? state.skills.map((item) => `
      <article class="capability-card">
        <div class="capability-card-head">
          <div class="capability-card-title"><span class="capability-type">Skill</span><h3>${escapeHtml(item.name)}</h3></div>
          ${statusPill(item.callable_by_agent ? "Agent 可调用" : "暂不可用", item.callable_by_agent ? "ready" : "warning")}
        </div>
        <p class="capability-description">${escapeHtml(item.description || "暂无说明")}</p>
        <div class="capability-badges" aria-label="能力状态">
          ${statusBadge("已导入", item.imported)}
          ${statusBadge("已启用", item.enabled)}
          ${statusBadge("可执行", item.executable)}
          ${statusBadge("已注册 Tool", item.registered_as_tool)}
        </div>
        <p class="capability-reason ${item.callable_by_agent ? "is-ready" : "is-warning"}">${escapeHtml(item.unavailable_reason || "已进入本轮 Agent Tool 列表。")}</p>
        <div class="capability-actions">
          <button class="ghost-button compact-button" data-skill-inspect="${escapeHtml(item.name)}">查看内部文件</button>
        </div>
      </article>
    `).join("")
    : `<div class="run-info-empty">当前没有 Skill。</div>`;
}

function renderMcpServers() {
  elements.capabilityMcpList.innerHTML = state.mcpServers.length
    ? state.mcpServers.map((item) => `
      <article class="capability-card">
        <div class="capability-card-head">
          <div class="capability-card-title"><span class="capability-type">MCP</span><h3>${escapeHtml(item.name)}</h3></div>
          ${statusPill(mcpStatusLabel(item.status), mcpStatusTone(item.status))}
        </div>
        <p class="capability-description capability-endpoint">${escapeHtml(item.transport_type === "stdio" ? [item.command, ...(item.args || [])].filter(Boolean).join(" ") : item.url || "未配置地址")}</p>
        <div class="capability-counts"><span><strong>${item.tools?.length || 0}</strong>Tools</span><span><strong>${item.resources?.length || 0}</strong>Resources</span><span><strong>${item.prompts?.length || 0}</strong>Prompts</span></div>
        <p class="capability-reason ${item.status === "connected" ? "is-ready" : item.status === "error" ? "is-error" : "is-warning"}">${escapeHtml(item.last_error || (item.status === "connected" ? "连接正常，能力已同步。" : "当前未连接。"))}</p>
        <div class="capability-actions">
          <button class="ghost-button compact-button" data-mcp-action="connect" data-server-id="${escapeHtml(item.server_id)}">连接</button>
          <button class="ghost-button compact-button" data-mcp-action="sync" data-server-id="${escapeHtml(item.server_id)}">同步</button>
          <button class="ghost-button compact-button" data-mcp-action="disconnect" data-server-id="${escapeHtml(item.server_id)}">断开</button>
          <button class="ghost-button compact-button" data-mcp-action="toggle" data-server-id="${escapeHtml(item.server_id)}">${item.enabled ? "停用" : "启用"}</button>
          <button class="ghost-button compact-button" data-mcp-action="edit" data-server-id="${escapeHtml(item.server_id)}">编辑</button>
          <button class="ghost-button compact-button" data-mcp-action="delete" data-server-id="${escapeHtml(item.server_id)}">删除</button>
        </div>
      </article>
    `).join("")
    : `<div class="run-info-empty">还没有 MCP Server，请先在左侧新增。</div>`;
}

function statusBadge(label, enabled) {
  return statusPill(enabled ? label : `未${label.replace(/^已/, "")}`, enabled ? "ready" : "muted", "capability-badge");
}

function statusPill(label, tone, extraClass = "") {
  const icon = tone === "ready" ? "✓" : tone === "error" || tone === "warning" ? "!" : "–";
  return `<span class="capability-status status-${tone} ${extraClass}"><span class="capability-status-icon" aria-hidden="true">${icon}</span>${escapeHtml(label)}</span>`;
}

function mcpStatusTone(status) {
  if (status === "connected") return "ready";
  if (status === "error") return "error";
  return status === "connecting" ? "warning" : "muted";
}

function mcpStatusLabel(status) {
  return {
    connected: "已连接",
    connecting: "连接中",
    error: "连接失败",
    disconnected: "未连接",
  }[status] || "未连接";
}

function riskLabel(riskLevel) {
  return {
    low: "低",
    medium: "中",
    high: "高",
    critical: "极高",
  }[String(riskLevel || "low").toLowerCase()] || String(riskLevel || "低");
}

function parseLines(value) {
  return String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}
