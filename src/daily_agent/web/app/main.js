import { cancelRun, listAttachments, request, requestChatStream, uploadAttachment } from "./api.js";
import { elements } from "./dom.js";
import { downloadMarkdownResult } from "./downloads.js";
import {
  appendMessage,
  renderConversation,
  renderEmptyConversation,
  renderRuns,
  renderSchedules,
  renderSessions,
  renderSkills,
  updateWelcomeStrip,
} from "./render.js";
import {
  applyRunFilters,
  handleAgentRunListClick,
  loadAgentRuns,
  moveRunPage,
  resetRunFilters,
} from "./run-view.js";
import { readRunLocation, updateWorkspaceLocation } from "./run-state.js";
import { initializeChatScroll } from "./chat-scroll.js";
import { shouldSubmitComposer } from "./composer-keyboard.js";
import {
  closeRunDrawer,
  isRunDrawerOpen,
  openLiveRunDrawer,
  openPersistedRunDrawer,
  renderPersistedRunDrawer,
  renderRunDrawerError,
} from "./run-drawer.js";
import {
  populateMcpForm,
  readMcpForm,
  renderCapabilityCenter,
  switchCapabilityTab,
} from "./capability-render.js";
import { state, DRAFT_STORAGE_PREFIX, WORKSPACE_STORAGE_KEY } from "./state.js";
import {
  autoResizeComposer,
  closeSessionNavigation,
  closeWorkspaceDrawers,
  closeWorkspaceNavigation,
  toggleSessionNavigation,
  toggleWorkspaceNavigation,
} from "./workspace-shell.js";
import {
  failActiveRun,
  finishActiveRun,
  markCancelRequested,
  markActiveRunStopped,
  renderDeveloperRawJson,
  renderPromptSnapshot,
  resetExecutionPanels,
  setComposerState,
  startActiveRun,
  stopActiveRunTimer,
} from "./trace.js";
import {
  closeModal,
  copyText,
  escapeHtml,
  openModal,
  readStoredJson,
  removeStoredValue,
  showToast,
  writeStoredJson,
} from "./utils.js";
export async function boot() {
  bindEvents();
  initializeChatScroll();
  hydrateWorkspaceState();
  const runLocation = readRunLocation(window.location.search);
  if (runLocation.sessionId) {
    elements.sessionId.value = runLocation.sessionId;
    state.preserveUnknownSession = true;
  }
  if (runLocation.view === "runs" || runLocation.runId) state.activeView = "runs";
  syncIdentity();
  switchView(state.activeView, { persist: false, updateLocation: false });
  loadDraftForCurrentSession();
  autoResizeComposer();
  renderEmptyConversation();
  setComposerState("idle", "随时可以发起一个任务");
  await refreshAll();
  if (state.activeView === "runs") {
    await loadAgentRuns({ selectRunId: runLocation.runId });
  }
}
function bindEvents() {
  elements.viewSwitches.forEach((button) => {
    button.addEventListener("click", async () => {
      switchView(button.dataset.view);
      if (button.dataset.view === "runs") await loadAgentRuns();
      if (button.dataset.view === "capabilities") await loadCapabilities();
    });
  });
  elements.promptButtons.forEach((button) => {
    button.addEventListener("click", () => fillComposer(button.dataset.prompt || ""));
  });
  elements.newChatButton.addEventListener("click", startNewChat);
  elements.topbarNewChat.addEventListener("click", startNewChat);
  elements.mobileSidebarToggle.addEventListener("click", toggleMobileSidebar);
  elements.workspaceMenuToggle.addEventListener("click", toggleMobileSidebar);
  elements.mobileSessionToggle.addEventListener("click", toggleMobileSessionPanel);
  elements.sessionPanelClose.addEventListener("click", () => {
    closeSessionNavigation({ restoreFocus: true });
  });
  elements.workspaceScrim.addEventListener("click", () => {
    closeWorkspaceDrawers({ restoreFocus: true });
  });
  elements.openModelConfig.addEventListener("click", openModelConfigModal);
  elements.openSkillsModal.addEventListener("click", async () => {
    await loadSkills();
    openModal(elements.skillsModal);
    if (state.selectedSkillName) await inspectSkill(state.selectedSkillName, { open: false });
  });
  elements.refreshCapabilities.addEventListener("click", loadCapabilities);
  elements.capabilityTabs.forEach((button) => button.addEventListener("click", () => switchCapabilityTab(button.dataset.capabilityTab)));
  elements.capabilityToolList.addEventListener("click", handleToolAction);
  elements.capabilitySkillList.addEventListener("click", handleCapabilitySkillAction);
  elements.capabilityMcpList.addEventListener("click", handleMcpAction);
  elements.mcpServerForm.addEventListener("submit", submitMcpServer);
  elements.resetMcpForm.addEventListener("click", () => populateMcpForm(null));
  elements.skillSearch.addEventListener("input", () => {
    state.skillSearch = elements.skillSearch.value.trim();
    renderSkills({ adoptSkill, inspectSkill, removeSkill, toggleSkill });
  });
  elements.refreshSessions.addEventListener("click", () => loadSessions());
  elements.refreshSchedules.addEventListener("click", loadSchedules);
  elements.refreshAgentRuns.addEventListener("click", () => loadAgentRuns());
  elements.loadRuns.addEventListener("click", loadSelectedRuns);
  elements.runFilterForm.addEventListener("submit", applyRunFilters);
  elements.resetRunFilters.addEventListener("click", resetRunFilters);
  elements.runPrevPage.addEventListener("click", () => moveRunPage(-1));
  elements.runNextPage.addEventListener("click", () => moveRunPage(1));
  elements.agentRunList.addEventListener("click", handleAgentRunListClick);
  elements.sessionSearch.addEventListener("input", () => {
    state.sessionSearch = elements.sessionSearch.value.trim();
    renderSessions({ openSession });
  });
  elements.userId.addEventListener("change", handleIdentityChange);
  elements.sessionId.addEventListener("change", handleSessionChange);
  elements.chatForm.addEventListener("submit", submitChat);
  elements.chatInput.addEventListener("input", () => {
    saveCurrentDraft();
    autoResizeComposer();
  });
  elements.attachFileButton?.addEventListener("click", () => elements.attachmentInput?.click());
  elements.attachmentInput?.addEventListener("change", uploadSelectedAttachments);
  elements.attachmentTray?.addEventListener("click", handleAttachmentTrayClick);
  elements.chatInput.addEventListener("keydown", handleComposerKeydown);
  elements.chatLog.addEventListener("click", handleMessageAction);
  elements.runDrawerClose.addEventListener("click", closeRunDrawer);
  elements.runDrawerBackdrop.addEventListener("click", (event) => {
    if (event.target === elements.runDrawerBackdrop) closeRunDrawer();
  });
  elements.skillImportForm.addEventListener("submit", importSkillFromPath);
  elements.modelConfigForm.addEventListener("submit", saveModelConfig);
  elements.testModelConnection.addEventListener("click", testModelConnection);
  elements.developerTabs.forEach((button) => {
    button.addEventListener("click", () => switchDeveloperTab(button.dataset.developerTab));
  });
  elements.copyPromptButton.addEventListener("click", copyLatestPrompt);
  elements.modalCloseButtons.forEach((button) => {
    button.addEventListener("click", () => closeModal(document.getElementById(button.dataset.closeModal)));
  });
  [elements.skillsModal, elements.modelConfigModal, elements.developerModal].forEach((modal) => {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeModal(modal);
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (isRunDrawerOpen()) closeRunDrawer();
      closeWorkspaceDrawers({ restoreFocus: true });
      [elements.skillsModal, elements.modelConfigModal, elements.developerModal].forEach((modal) => {
        if (!modal.hidden) closeModal(modal);
      });
    }
  });
}
async function refreshAll() {
  await Promise.all([
    loadHealth(),
    loadCapabilities(),
    loadModelConfig(),
    loadSchedules(),
    loadSessions(),
  ]);
}
function switchView(viewName, { persist = true, updateLocation = true } = {}) {
  state.activeView = viewName;
  elements.viewSwitches.forEach((button) => button.classList.toggle("active", button.dataset.view === viewName));
  elements.views.forEach((section) => section.classList.toggle("active", section.id === `view-${viewName}`));
  if (persist) {
    persistWorkspaceState();
  }
  if (updateLocation) updateWorkspaceLocation(viewName, viewName === "runs" ? undefined : "", state.sessionId);
  collapseMobileSidebar();
  if (viewName !== "chat") collapseMobileSessionPanel();
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
}
function toggleMobileSidebar() {
  toggleWorkspaceNavigation();
}
function collapseMobileSidebar() {
  closeWorkspaceNavigation();
}
function toggleMobileSessionPanel() {
  toggleSessionNavigation();
}
function collapseMobileSessionPanel() {
  closeSessionNavigation();
}
function syncIdentity() {
  state.userId = elements.userId.value.trim() || "default";
  state.sessionId = elements.sessionId.value.trim() || "work";
  elements.sidebarUserLabel.textContent = state.userId;
  persistWorkspaceState();
}
function hydrateWorkspaceState() {
  const saved = readStoredJson(WORKSPACE_STORAGE_KEY, null);
  if (!saved) return;
  if (saved.userId) elements.userId.value = saved.userId;
  if (saved.sessionId) {
    elements.sessionId.value = saved.sessionId;
    state.preserveUnknownSession = true;
  }
  if (saved.activeView && elements.views.some((section) => section.id === `view-${saved.activeView}`)) {
    state.activeView = saved.activeView;
  }
}
function persistWorkspaceState() {
  writeStoredJson(WORKSPACE_STORAGE_KEY, {
    userId: state.userId,
    sessionId: state.sessionId,
    activeView: state.activeView,
  });
}
function draftStorageKey() {
  return `${DRAFT_STORAGE_PREFIX}.${state.userId}.${state.sessionId}`;
}
function loadDraftForCurrentSession() {
  if (state.activeRun) return;
  const draft = readStoredJson(draftStorageKey(), "");
  elements.chatInput.value = typeof draft === "string" ? draft : "";
  autoResizeComposer();
  updateDraftStatus();
}
function saveCurrentDraft() {
  const draft = elements.chatInput.value;
  if (draft.trim()) writeStoredJson(draftStorageKey(), draft);
  else removeStoredValue(draftStorageKey());
  updateDraftStatus();
}
function clearCurrentDraft() {
  removeStoredValue(draftStorageKey());
  updateDraftStatus();
}
function updateDraftStatus() {
  elements.draftStatus.hidden = !elements.chatInput.value.trim();
}
async function loadHealth() {
  try {
    const payload = await request("/health");
    const hasError = payload.checks.some((item) => item.status === "error");
    const hasWarn = payload.checks.some((item) => item.status === "warn");
    const healthText = hasError ? "依赖异常" : hasWarn ? "部分告警" : "服务正常";
    const commit = String(payload.runtime?.commit || "").slice(0, 8);
    elements.sidebarHealthLabel.textContent = `本地服务 · ${healthText}${commit ? ` · ${commit}` : ""}`;
    elements.sidebarHealthLabel.title = payload.runtime?.started_at
      ? `版本 ${payload.runtime?.version || "未知"}，启动于 ${payload.runtime.started_at}`
      : "";
  } catch (error) {
    elements.sidebarHealthLabel.textContent = "本地服务 · 不可用";
    showToast(error.message, true);
  }
}
async function loadSkills() {
  try {
    const payload = await request("/skills");
    state.skills = payload.items || [];
    renderSkills({ adoptSkill, inspectSkill, removeSkill, toggleSkill });
    renderCapabilityCenter();
  } catch (error) {
    elements.skillsSidebarSummary.textContent = "读取失败";
    elements.skillsModalList.innerHTML = "";
  }
}
async function loadCapabilities() {
  try {
    const [toolsPayload, mcpPayload] = await Promise.all([
      request("/tools"),
      request("/mcp/servers"),
      loadSkills(),
    ]);
    state.tools = toolsPayload.items || [];
    state.mcpServers = Array.isArray(mcpPayload) ? mcpPayload : [];
    renderCapabilityCenter();
  } catch (error) {
    elements.capabilityToolList.innerHTML = `<div class="run-info-empty">能力读取失败：${escapeHtml(error.message)}</div>`;
  }
}
async function handleToolAction(event) {
  const button = event.target.closest("button[data-tool-action]");
  if (!button) return;
  try {
    await request(`/tools/${encodeURIComponent(button.dataset.toolId)}/${button.dataset.toolAction}`, { method: "POST" });
    await loadCapabilities();
    showToast("Tool 状态已更新。");
  } catch (error) {
    showToast(error.message, true);
  }
}
async function handleCapabilitySkillAction(event) {
  const button = event.target.closest("button[data-skill-inspect]");
  if (!button) return;
  await inspectSkill(button.dataset.skillInspect || "");
}
async function inspectSkill(skillName, { open = true } = {}) {
  if (!skillName) return;
  state.selectedSkillName = skillName;
  if (open) openModal(elements.skillsModal);
  if (state.skillDetails[skillName]) {
    renderSkills({ adoptSkill, inspectSkill, removeSkill, toggleSkill });
    return;
  }
  state.skillDetailLoadingName = skillName;
  delete state.skillDetailErrors[skillName];
  renderSkills({ adoptSkill, inspectSkill, removeSkill, toggleSkill });
  try {
    state.skillDetails[skillName] = await request(`/skills/${encodeURIComponent(skillName)}/detail`);
  } catch (error) {
    state.skillDetailErrors[skillName] = error.message;
  } finally {
    if (state.skillDetailLoadingName === skillName) state.skillDetailLoadingName = "";
    renderSkills({ adoptSkill, inspectSkill, removeSkill, toggleSkill });
  }
}
async function submitMcpServer(event) {
  event.preventDefault();
  const payload = readMcpForm();
  if (!payload.name) {
    showToast("请填写 MCP 名称。", true);
    return;
  }
  const serverId = state.selectedMcpServerId;
  try {
    await request(serverId ? `/mcp/servers/${encodeURIComponent(serverId)}` : "/mcp/servers", {
      method: serverId ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    populateMcpForm(null);
    await loadCapabilities();
    showToast(serverId ? "MCP 配置已更新。" : "MCP Server 已新增。");
  } catch (error) {
    showToast(error.message, true);
  }
}
async function handleMcpAction(event) {
  const button = event.target.closest("button[data-mcp-action]");
  if (!button) return;
  const server = state.mcpServers.find((item) => item.server_id === button.dataset.serverId);
  if (!server) return;
  const action = button.dataset.mcpAction;
  if (action === "edit") {
    populateMcpForm(server);
    return;
  }
  try {
    if (action === "delete") {
      await request(`/mcp/servers/${encodeURIComponent(server.server_id)}`, { method: "DELETE" });
    } else if (action === "toggle") {
      await request(`/mcp/servers/${encodeURIComponent(server.server_id)}`, {
        method: "PUT",
        body: JSON.stringify(mcpPayloadFromServer(server, !server.enabled)),
      });
    } else {
      await request(`/mcp/servers/${encodeURIComponent(server.server_id)}/${action}`, { method: "POST" });
    }
    await loadCapabilities();
    showToast("MCP 状态已更新。");
  } catch (error) {
    await loadCapabilities();
    showToast(error.message, true);
  }
}
function mcpPayloadFromServer(server, enabled) {
  return {
    name: server.name,
    transport_type: server.transport_type,
    command: server.command,
    args: server.args || [],
    url: server.url,
    environment_keys: server.environment_keys || [],
    header_env_map: server.header_env_map || {},
    enabled,
  };
}
async function loadModelConfig() {
  try {
    const payload = await request("/settings/model");
    state.modelConfig = payload;
    applyModelConfigToUi(payload);
  } catch (error) {
    state.modelConfig = null;
    elements.sidebarModelLabel.textContent = "模型未知";
    elements.modelConfigMeta.textContent = `读取失败：${error.message}`;
  }
}
async function openModelConfigModal() {
  if (!state.modelConfig) {
    await loadModelConfig();
  }
  openModal(elements.modelConfigModal);
}
async function saveModelConfig(event) {
  event.preventDefault();
  try {
    const payload = await request("/settings/model", {
      method: "POST",
      body: JSON.stringify({
        provider: elements.modelProvider.value,
        model_id: elements.modelId.value.trim() || "deepseek-v4-flash",
        api_key: elements.modelApiKey.value,
        base_url: elements.modelBaseUrl.value.trim() || "https://api.deepseek.com",
        clear_api_key: elements.modelClearApiKey.checked,
        agno_telemetry: elements.agnoTelemetry.checked,
        thinking_enabled: elements.modelThinkingEnabled.checked,
        reasoning_effort: elements.modelReasoningEffort.value || "high",
        context_token_budget: Number(elements.contextTokenBudget.value) || 1000000,
      }),
    });
    state.modelConfig = payload;
    applyModelConfigToUi(payload);
    closeModal(elements.modelConfigModal);
    showToast("模型配置已保存。");
  } catch (error) {
    showToast(error.message, true);
  }
}
function applyModelConfigToUi(payload) {
  const modelId = payload?.effective?.model_id || payload?.config?.model_id || "模型未知";
  elements.sidebarModelLabel.textContent = modelId;
  elements.modelProvider.value = payload?.config?.provider || "openai_compatible";
  elements.modelId.value = payload?.config?.model_id || "";
  elements.modelApiKey.value = "";
  elements.modelApiKey.placeholder = payload?.effective?.api_key_configured
    ? "已配置，留空保存会保留原 Key"
    : "尚未配置 API Key";
  elements.modelBaseUrl.value = payload?.config?.base_url || "";
  elements.modelClearApiKey.checked = false;
  elements.agnoTelemetry.checked = Boolean(payload?.config?.agno_telemetry);
  elements.modelThinkingEnabled.checked = payload?.config?.thinking_enabled !== false;
  elements.modelReasoningEffort.value = payload?.config?.reasoning_effort || "high";
  elements.contextTokenBudget.value = String(
    payload?.config?.context_token_budget || payload?.effective?.context_token_budget || 1000000
  );
  elements.modelConfigMeta.textContent = `配置文件：${payload.config_path}${payload.config_exists ? "" : "（当前不存在，将在保存时创建）"}`;
  const activeKeys = Object.entries(payload?.env_overrides || {})
    .filter(([, enabled]) => enabled)
    .map(([key]) => key);
  elements.modelConfigNote.textContent = activeKeys.length
    ? `注意：以下环境变量正在覆盖文件配置：${activeKeys.join("、")}。`
    : "当前没有检测到模型或上下文预算相关环境变量覆盖。";
}
async function testModelConnection() {
  elements.testModelConnection.disabled = true;
  elements.testModelConnection.textContent = "测试中...";
  try {
    const payload = await request("/settings/model/test", { method: "POST" });
    const success = payload.status === "ok";
    showToast(`${payload.detail}（${payload.duration_ms}ms）`, !success);
    await loadHealth();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.testModelConnection.disabled = false;
    elements.testModelConnection.textContent = "测试连接";
  }
}
async function handleIdentityChange() {
  syncIdentity();
  loadDraftForCurrentSession();
  await Promise.all([loadSessions(), loadSchedules(), state.activeView === "runs" ? loadAgentRuns() : Promise.resolve()]);
}
async function handleSessionChange() {
  syncIdentity();
  loadDraftForCurrentSession();
  await loadSessionDetail(state.sessionId);
}
async function loadSessions({ refreshDetail = true } = {}) {
  syncIdentity();
  try {
    const payload = await request(`/sessions?user_id=${encodeURIComponent(state.userId)}&limit=20`);
    state.sessions = payload.items || [];
    renderSessions({ openSession });
    const knownSession = state.sessions.find((item) => item.session_id === state.sessionId);
    if (knownSession && refreshDetail) {
      await loadSessionDetail(state.sessionId, { preservePlan: true });
    } else if (!knownSession && state.sessions.length > 0 && !state.preserveUnknownSession) {
      state.sessionId = state.sessions[0].session_id;
      elements.sessionId.value = state.sessionId;
      await loadSessionDetail(state.sessionId, { preservePlan: true });
    } else if (!knownSession && state.preserveUnknownSession) {
      await loadAttachmentsForCurrentSession();
      renderEmptyConversation("这是一个尚未产生消息的新对话，发送第一条消息后会出现在会话列表中。");
    } else if (!state.sessions.length) {
      renderEmptyConversation();
    }
  } catch (error) {
    elements.sessionList.innerHTML = `<div class="session-item"><strong>加载失败</strong><p>${error.message}</p></div>`;
  }
}
async function openSession(sessionId) {
  state.preserveUnknownSession = true;
  await loadSessionDetail(sessionId);
  collapseMobileSessionPanel();
  switchView("chat");
}
async function loadSessionDetail(sessionId, { preservePlan = false } = {}) {
  syncIdentity();
  if (!sessionId) {
    renderEmptyConversation();
    return;
  }
  try {
    const previousSessionId = state.sessionId;
    const payload = await request(`/sessions/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(state.userId)}&limit=100`);
    state.messages = payload.messages || [];
    state.summary = payload.summary || null;
    state.contextObservation = payload.context_observation || null;
    state.sessionId = sessionId;
    elements.sessionId.value = sessionId;
    persistWorkspaceState();
    updateWorkspaceLocation("chat", "", sessionId);
    loadDraftForCurrentSession();
    await loadAttachmentsForCurrentSession();
    renderSessions({ openSession });
    renderConversation();
    if (!preservePlan || previousSessionId !== sessionId) {
      resetExecutionPanels("已加载历史");
    }
  } catch (error) {
    renderEmptyConversation(`读取会话失败：${error.message}`);
  }
}
function startNewChat() {
  stopActiveRunTimer();
  state.activeRun = null;
  state.currentAbortController = null;
  state.sessionId = `session-${Date.now()}`;
  state.preserveUnknownSession = true;
  state.messages = [];
  state.summary = null;
  state.contextObservation = null;
  state.attachments = [];
  state.selectedAttachmentIds = [];
  elements.sessionId.value = state.sessionId;
  persistWorkspaceState();
  updateWorkspaceLocation("chat", "", state.sessionId);
  loadDraftForCurrentSession();
  if (elements.attachmentStatus) elements.attachmentStatus.hidden = true;
  renderAttachmentTray();
  resetExecutionPanels("未开始");
  renderSessions({ openSession });
  renderEmptyConversation("新对话已准备好，直接输入第一条消息即可。");
  collapseMobileSessionPanel();
  switchView("chat");
  elements.chatInput.focus();
}
async function submitChat(event) {
  event.preventDefault();
  syncIdentity();
  const query = elements.chatInput.value.trim();
  if (!query) {
    showToast("先输入一条消息。", true);
    return;
  }
  if (state.activeRun) {
    showToast("上一轮还在执行，先等它完成。", true);
    return;
  }
  elements.chatInput.value = "";
  autoResizeComposer();
  clearCurrentDraft();
  await sendChatQuery(query);
}
async function sendChatQuery(query, { appendUserMessage = true } = {}) {
  if (state.activeRun) {
    showToast("上一轮还在执行，先等它完成。", true);
    return;
  }
  if (appendUserMessage) {
    appendMessage({ role: "user", content: query, created_at: new Date().toISOString() });
  }
  startActiveRun(query);
  updateWelcomeStrip();
  try {
    const payload = await requestChatStream(query);
    finishActiveRun(payload);
    state.selectedAttachmentIds = [];
    renderAttachmentTray();
    await loadSessions({ refreshDetail: false });
    return true;
  } catch (error) {
    if (error.name === "AbortError") {
      markActiveRunStopped();
      appendMessage({ role: "assistant", content: "停止接口调用失败，页面已经停止接收结果。请到运行监控确认后台状态。", created_at: new Date().toISOString() });
      showToast("没有确认后台已停止，请到运行监控查看。", true);
      return false;
    }
    failActiveRun(error);
    appendMessage({ role: "assistant", content: `请求失败：${error.message}`, created_at: new Date().toISOString() });
    showToast(error.message, true);
    return false;
  } finally {
    updateWelcomeStrip();
  }
}
async function uploadSelectedAttachments() {
  const files = Array.from(elements.attachmentInput.files || []);
  elements.attachmentInput.value = "";
  if (!files.length) return;
  elements.attachmentStatus.hidden = false;
  elements.attachmentStatus.textContent = `正在上传 ${files.length} 个附件...`;
  try {
    for (const file of files.slice(0, 10)) {
      const attachment = await uploadAttachment(file);
      state.attachments = [...state.attachments.filter((item) => item.id !== attachment.id), attachment];
      state.selectedAttachmentIds.push(attachment.id);
    }
    state.selectedAttachmentIds = [...new Set(state.selectedAttachmentIds)];
    renderAttachmentTray();
    elements.attachmentStatus.textContent = "附件已上传并解析";
  } catch (error) {
    showToast(error.message, true);
    elements.attachmentStatus.textContent = "附件上传失败";
  }
}
async function loadAttachmentsForCurrentSession() {
  try {
    const payload = await listAttachments();
    state.attachments = payload.items || [];
    state.selectedAttachmentIds = [];
    renderAttachmentTray();
  } catch {
    state.attachments = [];
    state.selectedAttachmentIds = [];
    renderAttachmentTray();
  }
}
function handleAttachmentTrayClick(event) {
  const button = event.target.closest("[data-attachment-id]");
  if (!button) return;
  const attachmentId = button.dataset.attachmentId;
  if (state.selectedAttachmentIds.includes(attachmentId)) {
    state.selectedAttachmentIds = state.selectedAttachmentIds.filter((item) => item !== attachmentId);
  } else {
    state.selectedAttachmentIds.push(attachmentId);
  }
  renderAttachmentTray();
}
function renderAttachmentTray() {
  if (!elements.attachmentTray) return;
  elements.attachmentTray.hidden = !state.attachments.length;
  elements.attachmentTray.innerHTML = state.attachments.map((item) => {
    const selected = state.selectedAttachmentIds.includes(item.id);
    const status = item.parse_status === "success" ? "已解析" : item.parse_status === "partial" ? "部分解析" : "解析失败";
    return `<button type="button" class="attachment-chip ${selected ? "selected" : ""}" data-attachment-id="${escapeHtml(item.id)}" title="${escapeHtml(item.parse_message || "")}"><strong>${escapeHtml(item.filename)}</strong><span>${status}${selected ? " · 本轮使用" : ""}</span></button>`;
  }).join("");
}
function handleComposerKeydown(event) {
  if (!shouldSubmitComposer(event)) return;
  event.preventDefault();
  elements.chatForm.requestSubmit();
}
async function loadSchedules() {
  syncIdentity();
  try {
    const payload = await request(`/schedules?user_id=${encodeURIComponent(state.userId)}`);
    renderSchedules(payload.items || [], { handleScheduleAction });
  } catch (error) {
    elements.scheduleList.innerHTML = `<div class="schedule-empty-state is-error"><strong>任务加载失败</strong><p>${escapeHtml(error.message)}</p><button type="button" class="ghost-button" data-schedule-retry>重新加载</button></div>`;
    elements.scheduleList.querySelector("[data-schedule-retry]")?.addEventListener("click", loadSchedules);
  }
}
async function loadSelectedRuns() {
  const jobId = Number(elements.runsJobId.value);
  if (!jobId) {
    showToast("先输入一个任务 ID。", true);
    return;
  }
  try {
    const payload = await request(`/schedules/${jobId}/runs?limit=20`);
    renderRuns(payload.items || []);
    switchView("runs");
    document.querySelector(".automatic-run-panel").open = true;
  } catch (error) {
    elements.runsTable.innerHTML = `<tr><td colspan="5">加载失败：${error.message}</td></tr>`;
  }
}
async function handleScheduleAction(action, jobId) {
  if (action === "show-runs") {
    elements.runsJobId.value = String(jobId);
    await loadSelectedRuns();
    return;
  }
  if (action === "run-now") {
    await triggerRunNow(jobId);
    return;
  }
  await toggleSchedule(jobId, action);
}
async function toggleSchedule(jobId, action) {
  syncIdentity();
  const endpoint = action === "resume" ? "resume" : "pause";
  try {
    await request(`/schedules/${jobId}/${endpoint}?user_id=${encodeURIComponent(state.userId)}`, { method: "POST" });
    showToast(action === "resume" ? "任务已恢复。" : "任务已暂停。");
    await loadSchedules();
  } catch (error) {
    showToast(error.message, true);
  }
}
async function triggerRunNow(jobId) {
  syncIdentity();
  try {
    const payload = await request(`/schedules/${jobId}/run-now?user_id=${encodeURIComponent(state.userId)}`, { method: "POST" });
    showToast(payload.run.status === "success" ? "任务已执行。" : "任务执行失败。", payload.run.status !== "success");
    elements.runsJobId.value = String(jobId);
    await Promise.all([loadSelectedRuns(), loadSchedules()]);
  } catch (error) {
    showToast(error.message, true);
  }
}
async function importSkillFromPath(event) {
  event.preventDefault();
  const path = elements.skillImportPath.value.trim();
  if (!path) {
    showToast("先填写本地 skill 路径或 GitHub URL。", true);
    return;
  }
  try {
    const payload = await request("/skills/import", {
      method: "POST",
      body: JSON.stringify({ path, source_url: isGitHubUrl(path) ? path : null, enable: true }),
    });
    elements.skillImportPath.value = "";
    state.skillDetails = {};
    showToast((payload.items?.length || 0) > 1 ? `已导入 ${payload.items.length} 个 skill。` : "skill 已导入。");
    await loadSkills();
  } catch (error) {
    showToast(error.message, true);
  }
}
function isGitHubUrl(value) {
  return /^https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/?$/.test(value)
    || /^git@github\.com:[^/\s]+\/[^/\s]+(?:\.git)?$/.test(value)
    || /^https:\/\/(?:www\.)?skills\.sh\/[^/\s]+\/[^/\s]+\/[^/\s]+\/?$/.test(value);
}
async function toggleSkill(skillName, action) {
  const endpoint = action === "enable" ? "enable" : "disable";
  try {
    await request(`/skills/${encodeURIComponent(skillName)}/${endpoint}`, { method: "POST" });
    showToast(action === "enable" ? "skill 已启用。" : "skill 已停用。");
    await loadSkills();
  } catch (error) {
    showToast(error.message, true);
  }
}
async function removeSkill(skillName) {
  try {
    await request(`/skills/${encodeURIComponent(skillName)}`, { method: "DELETE" });
    delete state.skillDetails[skillName];
    delete state.skillDetailErrors[skillName];
    showToast("skill 已删除。");
    await loadSkills();
  } catch (error) {
    showToast(error.message, true);
  }
}
async function adoptSkill(skillName) {
  try {
    await request(`/skills/${encodeURIComponent(skillName)}/adopt`, { method: "POST" });
    delete state.skillDetails[skillName];
    showToast("skill 已迁移到受管目录。");
    await loadSkills();
  } catch (error) {
    showToast(error.message, true);
  }
}
async function handleMessageAction(event) {
  const activeRunAction = event.target.closest("[data-active-run-action]");
  if (activeRunAction) {
    if (activeRunAction.dataset.activeRunAction === "stop") await stopCurrentRun();
    if (activeRunAction.dataset.activeRunAction === "details") openLiveRunDrawer(state.activeRun);
    return;
  }
  const confirmationButton = event.target.closest("[data-confirmation-action]");
  if (confirmationButton) {
    await handleConfirmationAction(confirmationButton);
    return;
  }
  const activeDownloadButton = event.target.closest("[data-active-message-action='download']");
  if (activeDownloadButton) {
    const content = state.activeRun?.streamedText || state.latestResultPayload?.response_text || "";
    if (!content) {
      showToast("当前回答还没有可下载内容。", true);
      return;
    }
    downloadMarkdownResult(content, state.activeRun?.runId || `answer-${Date.now()}`);
    showToast("已下载 Markdown 结果。");
    return;
  }
  const runDetailButton = event.target.closest("[data-run-id]");
  if (runDetailButton) {
    const runId = runDetailButton.dataset.runId || "";
    openPersistedRunDrawer(runId);
    try {
      const detail = await request(`/runs/${encodeURIComponent(runId)}?user_id=${encodeURIComponent(state.userId)}`);
      renderPersistedRunDrawer(detail);
    } catch (error) {
      renderRunDrawerError(runId, error.message);
    }
    return;
  }
  const runActionButton = event.target.closest("[data-run-action]");
  if (runActionButton) {
    openDeveloperModal(runActionButton.dataset.runAction === "prompt" ? "prompt" : "trace");
    return;
  }
  const followupButton = event.target.closest("[data-followup-prompt]");
  if (followupButton) {
    fillComposer(followupButton.dataset.followupPrompt || "");
    showToast("已填入追问。");
    return;
  }
  const button = event.target.closest("[data-message-action]");
  if (!button) return;
  const message = state.messages[Number(button.dataset.messageIndex)];
  if (!message) {
    showToast("没有找到这条消息。", true);
    return;
  }
  const content = message.content || "";
  const action = button.dataset.messageAction;
  if (action === "copy") {
    await copyText(content);
    showToast("已复制消息。");
  } else if (action === "reuse") {
    fillComposer(content);
    showToast("已放入输入框。");
  } else if (action === "download") {
    downloadMarkdownResult(content, message.run_id || `answer-${Date.now()}`);
    showToast("已下载 Markdown 结果。");
  } else if (action === "retry") {
    await sendChatQuery(content.trim());
  }
}

async function handleConfirmationAction(button) {
  if (state.activeRun) {
    showToast("上一轮还在执行，请稍等。", true);
    return;
  }
  const action = button.dataset.confirmationAction;
  if (!["confirm", "cancel"].includes(action)) return;
  const group = button.closest(".confirmation-actions");
  const buttons = [...(group?.querySelectorAll("button[data-confirmation-action]") || [])];
  buttons.forEach((item) => { item.disabled = true; });
  button.textContent = action === "confirm" ? "正在确认…" : "正在取消…";
  const succeeded = await sendChatQuery(action === "confirm" ? "确认" : "取消", { appendUserMessage: false });
  if (succeeded && group?.isConnected) {
    group.innerHTML = `<p class="confirmation-result" role="status">${action === "confirm" ? "已确认，Agent 正在继续执行。" : "已取消这次操作。"}</p>`;
  } else if (!succeeded && button.isConnected) {
    buttons.forEach((item) => { item.disabled = false; });
    button.textContent = action === "confirm" ? "确认执行" : "取消";
  }
}
function fillComposer(text) {
  if (state.activeRun) return showToast("Agent 回复期间不会自动改写输入框。", true);
  elements.chatInput.value = text;
  autoResizeComposer();
  saveCurrentDraft();
  elements.chatInput.focus();
  const end = elements.chatInput.value.length;
  elements.chatInput.setSelectionRange(end, end);
}
async function stopCurrentRun() {
  const runId = String(state.activeRun?.runId || "").trim();
  if (!runId || state.activeRun?.cancelRequested) {
    showToast(runId ? "正在停止，请稍等。" : "运行编号还没返回，请稍后再点停止。", !runId);
    return;
  }
  markCancelRequested("正在提交停止请求...");
  try {
    const payload = await cancelRun(runId);
    if (!payload.accepted) {
      throw new Error(payload.message || "任务已经结束，不能再停止。");
    }
    markCancelRequested(payload.message || "正在等待当前执行步骤结束...");
  } catch (error) {
    showToast(`停止失败：${error.message}`, true);
    if (state.currentAbortController) {
      state.currentAbortController.abort();
    }
  }
}
function openDeveloperModal(tab = state.activeDeveloperTab || "prompt") {
  switchDeveloperTab(tab);
  renderPromptSnapshot();
  renderDeveloperRawJson();
  openModal(elements.developerModal);
}
function switchDeveloperTab(tab) {
  const nextTab = tab || "prompt";
  state.activeDeveloperTab = nextTab;
  elements.developerTabs.forEach((button) => {
    button.classList.toggle("active", button.dataset.developerTab === nextTab);
  });
  elements.developerPanels.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.developerPanel === nextTab);
  });
}
async function copyLatestPrompt() {
  const promptText = state.latestPromptSnapshot?.prompt_text || "";
  if (!promptText.trim()) {
    showToast("当前还没有可复制的 Prompt。", true);
    return;
  }
  await copyText(promptText);
  showToast("Prompt 已复制。");
}
