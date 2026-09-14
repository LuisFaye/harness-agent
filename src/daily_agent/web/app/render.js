import { elements } from "./dom.js";
import { state } from "./state.js";
import { renderMarkdown } from "./markdown.js";
import { scrollChatToLatest } from "./chat-scroll.js";
import {
  compactText,
  escapeHtml,
  formatClock,
  formatDateTime,
  formatShortTime,
  truncateText,
} from "./utils.js";

export function renderSkills(callbacks) {
  if (!state.skills.length) {
    elements.skillsSidebarSummary.textContent = "当前无技能";
    elements.skillsManagerCount.textContent = "0 个 skill";
    elements.skillsModalList.innerHTML = `<article class="skill-modal-item"><p>当前没有可展示的 skill。</p></article>`;
    return;
  }

  if (!state.selectedSkillName || !state.skills.some((item) => item.name === state.selectedSkillName)) {
    state.selectedSkillName = state.skills[0]?.name || "";
  }

  const filteredSkills = filterSkills(state.skills, state.skillSearch);
  if (filteredSkills.length && !filteredSkills.some((item) => item.name === state.selectedSkillName)) {
    state.selectedSkillName = filteredSkills[0].name;
  }
  const selectedSkill = filteredSkills.find((item) => item.name === state.selectedSkillName) || filteredSkills[0] || null;
  const enabledCount = state.skills.filter((item) => item.status === "enabled").length;
  elements.skillsSidebarSummary.textContent = `${enabledCount}/${state.skills.length} 个启用`;
  elements.skillsManagerCount.textContent = `${filteredSkills.length}/${state.skills.length} 个 skill · ${enabledCount} 个启用`;

  elements.skillsModalList.innerHTML = `
    <div class="skills-manager">
      <div class="skills-manager-list" role="listbox" aria-label="Skill 列表">
        ${filteredSkills.length ? filteredSkills.map((item) => renderSkillListItem(item, item.name === selectedSkill?.name)).join("") : `
          <div class="skills-empty">没有匹配的 skill。</div>
        `}
      </div>
      <section class="skills-manager-detail" aria-live="polite">
        ${selectedSkill ? renderSkillDetail(selectedSkill) : `<div class="skills-empty">请选择一个 skill。</div>`}
      </section>
    </div>
  `;

  elements.skillsModalList.querySelectorAll("[data-skill-select]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.selectedSkillName = button.dataset.skillSelect || "";
      renderSkills(callbacks);
      await callbacks.inspectSkill(state.selectedSkillName, { open: false });
    });
  });

  elements.skillsModalList.querySelectorAll("button[data-skill-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const skillName = button.dataset.skillName;
      const action = button.dataset.skillAction;
      if (action === "adopt") await callbacks.adoptSkill(skillName);
      else if (action === "remove") await callbacks.removeSkill(skillName);
      else if (action === "inspect") await callbacks.inspectSkill(skillName, { open: false });
      else await callbacks.toggleSkill(skillName, action);
    });
  });
}

function renderSkillListItem(item, selected) {
  const tools = item.tools || [];
  const meta = [
    skillSourceText(item.source),
    skillCompatibilityText(item.compatibility?.status),
    tools.length ? `${tools.length} 工具` : "无工具",
  ].filter(Boolean).join(" · ");
  return `
    <button type="button" class="skill-list-item ${selected ? "active" : ""}" data-skill-select="${escapeHtml(item.name)}" role="option" aria-selected="${selected ? "true" : "false"}">
      <div>
        <strong>${escapeHtml(item.name)}</strong>
        <p>${escapeHtml(compactText(item.description || "暂无说明", 92))}</p>
      </div>
      <span class="skill-badge ${escapeHtml(item.status || "available")}">${escapeHtml(skillStatusText(item.status))}</span>
      <small>${escapeHtml(meta)}</small>
    </button>
  `;
}

function renderSkillDetail(item) {
  const tools = item.tools || [];
  const capabilities = item.capabilities || [];
  const triggers = item.triggers || [];
  const examples = item.intent_examples || [];
  return `
    <article class="skill-detail-card">
      <div class="skill-detail-head">
        <div>
          <span class="skill-source-label">${escapeHtml(skillSourceText(item.source))}</span>
          <h3>${escapeHtml(item.name)}</h3>
          <p>${escapeHtml(item.description || "暂无说明")}</p>
        </div>
        <span class="skill-badge ${escapeHtml(item.status || "available")}">${escapeHtml(skillBadgeText(item))}</span>
      </div>
      <div class="skill-detail-grid">
        <div><span>能做什么</span><strong>${escapeHtml(skillPurposeText(item))}</strong></div>
        <div><span>执行方式</span><strong>${escapeHtml(skillExecutionText(item))}</strong></div>
        <div><span>风险</span><strong>${escapeHtml(skillRiskText(item.risk_level))}${item.requires_confirmation ? " · 执行前确认" : ""}</strong></div>
        <div><span>兼容性</span><strong>${escapeHtml(skillCompatibilityText(item.compatibility?.status))}</strong></div>
      </div>
      ${renderChipSection("工具", tools)}
      ${renderChipSection("触发词", triggers)}
      ${renderChipSection("能力", capabilities.map(skillCapabilityText))}
      ${renderChipSection("示例", examples, { muted: true })}
      ${renderSchemaPreview(item)}
      ${item.install_source ? renderSkillPathRow("来源地址", item.install_source) : ""}
      ${renderSkillPathRow("本地路径", item.path || "")}
      ${item.origin_path && item.origin_path !== item.path ? renderSkillPathRow("原始路径", item.origin_path) : ""}
      ${renderSkillInternalDetail(item)}
      ${item.error_message ? `<div class="notice">${escapeHtml(item.error_message)}</div>` : ""}
      ${(item.diagnostics || []).length ? `<div class="notice">${escapeHtml(item.diagnostics.join("；"))}</div>` : ""}
      <div class="skill-detail-actions">
        ${item.can_adopt ? `<button class="text-button" data-skill-action="adopt" data-skill-name="${escapeHtml(item.name)}">迁移接管</button>` : ""}
        <button class="primary-button compact-button" data-skill-action="${item.status === "enabled" ? "disable" : "enable"}" data-skill-name="${escapeHtml(item.name)}">
          ${item.status === "enabled" ? "停用 skill" : "启用 skill"}
        </button>
        ${item.can_remove ? `<button class="text-button danger" data-skill-action="remove" data-skill-name="${escapeHtml(item.name)}">删除</button>` : ""}
      </div>
    </article>
  `;
}

function renderSkillInternalDetail(item) {
  const detail = state.skillDetails[item.name];
  const loading = state.skillDetailLoadingName === item.name;
  const error = state.skillDetailErrors[item.name];
  if (loading) {
    return `
      <section class="skill-internal-section" aria-busy="true">
        <div class="skill-internal-head"><div><h4>内部文件</h4><p>正在安全读取 SKILL.md、清单和脚本…</p></div></div>
        <div class="skill-internal-state" role="status">读取中，请稍候。</div>
      </section>
    `;
  }
  if (error) {
    return `
      <section class="skill-internal-section">
        <div class="skill-internal-head"><div><h4>内部文件</h4><p>读取失败，不影响 Skill 的正常运行。</p></div></div>
        <div class="skill-internal-state error" role="alert">${escapeHtml(error)}</div>
        <button class="ghost-button compact-button" data-skill-action="inspect" data-skill-name="${escapeHtml(item.name)}">重新读取</button>
      </section>
    `;
  }
  if (!detail) {
    return `
      <section class="skill-internal-section">
        <div class="skill-internal-head">
          <div><h4>内部文件</h4><p>按需读取，不会展示 .env 等敏感文件，常见密钥会自动打码。</p></div>
          <button class="ghost-button compact-button" data-skill-action="inspect" data-skill-name="${escapeHtml(item.name)}">查看 SKILL.md 与内部文件</button>
        </div>
      </section>
    `;
  }
  const persistence = detail.persistence || {};
  return `
    <section class="skill-internal-section">
      <div class="skill-internal-head">
        <div><h4>内部文件</h4><p>共 ${detail.files?.length || 0} 个文件；文本已做敏感信息过滤。</p></div>
        <span class="capability-badge ok">安全读取完成</span>
      </div>
      <div class="skill-persistence-list">
        ${renderSkillPathRow("定义目录", persistence.definition_path || "")}
        ${renderSkillPathRow("启停状态", persistence.status_registry_path || "")}
        ${renderSkillPathRow("Tool 状态", persistence.tool_registry_path || "")}
      </div>
      <div class="skill-file-list">${renderSkillFiles(detail.files || [])}</div>
    </section>
  `;
}

function renderSkillFiles(files) {
  if (!files.length) {
    return `<div class="skill-internal-state">这个 Skill 没有可展示的约定文件。</div>`;
  }
  return files.map((file, index) => {
    const hasContent = typeof file.content === "string";
    const label = skillFileKindText(file.kind);
    return `
      <details class="skill-file" ${index === 0 && hasContent ? "open" : ""}>
        <summary>
          <span><strong>${escapeHtml(file.path)}</strong><small>${escapeHtml(label)} · ${escapeHtml(formatFileSize(file.size_bytes))}${file.truncated ? " · 内容已截断" : ""}</small></span>
          <span class="skill-file-toggle">展开</span>
        </summary>
        ${hasContent
          ? `<pre class="skill-file-content"><code>${escapeHtml(file.content)}</code></pre>`
          : `<div class="skill-asset-note">资源文件只展示名称和大小，不直接读取二进制内容。</div>`}
      </details>
    `;
  }).join("");
}

function skillFileKindText(kind) {
  return ({ instructions: "Skill 说明", manifest: "清单", script: "脚本", reference: "参考资料", asset: "资源" })[kind] || "文件";
}

function formatFileSize(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderChipSection(title, items, { muted = false } = {}) {
  const values = (items || []).filter(Boolean);
  if (!values.length) {
    return "";
  }
  return `
    <section class="skill-chip-section">
      <h4>${escapeHtml(title)}</h4>
      <div class="skill-chip-list">
        ${values.map((item) => `<span class="${muted ? "muted" : ""}">${escapeHtml(item)}</span>`).join("")}
      </div>
    </section>
  `;
}

function renderSkillPathRow(label, value) {
  return `
    <div class="skill-path-row">
      <span>${escapeHtml(label)}</span>
      <code title="${escapeHtml(value)}">${escapeHtml(truncateText(value, 92) || "无")}</code>
    </div>
  `;
}

function renderSchemaPreview(item) {
  const properties = item.input_schema?.properties || {};
  const entries = Object.entries(properties).slice(0, 8);
  if (!entries.length) {
    return "";
  }
  return `
    <section class="skill-chip-section">
      <h4>需要的输入</h4>
      <div class="skill-field-list">
        ${entries.map(([name, meta]) => `
          <div>
            <strong>${escapeHtml(name)}</strong>
            <span>${escapeHtml(compactText(meta?.description || meta?.title || meta?.type || "未说明", 100))}</span>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function skillPurposeText(item) {
  if (item.description) {
    return compactText(item.description, 120);
  }
  const tools = item.tools || [];
  if (tools.length) {
    return `提供 ${tools.join("、")} 工具能力`;
  }
  return "作为说明文档提供上下文，不直接执行工具。";
}

function skillExecutionText(item) {
  const type = item.execution?.type;
  if (type === "python_callable") {
    return "原生 Python handler";
  }
  if (type === "compatible_skill") {
    return "兼容第三方 SKILL.md";
  }
  if (item.compatibility?.status === "document_only") {
    return "仅文档，不直接执行";
  }
  return type || "未声明";
}

function skillCapabilityText(value) {
  const mapping = {
    shell: "Shell 命令",
    http: "HTTP 请求",
    browser: "浏览器自动化",
    web_search: "网页搜索",
    web_fetch: "网页读取",
  };
  return mapping[value] || value;
}

export function renderSessions(callbacks) {
  if (!state.sessions.length) {
    elements.sessionList.innerHTML = `<div class="session-item"><strong>暂无对话</strong><p>发送第一条消息后会出现在这里。</p></div>`;
    return;
  }

  const filteredSessions = filterSessions(state.sessions, state.sessionSearch);
  if (!filteredSessions.length) {
    elements.sessionList.innerHTML = `<div class="session-item"><strong>没有匹配</strong><p>换个关键词再试。</p></div>`;
    return;
  }

  elements.sessionList.innerHTML = filteredSessions.map((item) => {
    const title = truncateText(item.title || item.session_id, 30);
    const meta = item.session_id === state.sessionId
      ? `当前会话 · ${item.session_id}`
      : item.has_summary
        ? "自动摘要已生成"
        : `${formatShortTime(item.last_message_at)} · ${item.message_count || 0} 条消息`;
    return `
      <button type="button" class="session-item ${item.session_id === state.sessionId ? "active" : ""}" data-session-id="${escapeHtml(item.session_id)}">
        <strong>${escapeHtml(title)}</strong>
        <p>${escapeHtml(meta)}</p>
      </button>
    `;
  }).join("");

  elements.sessionList.querySelectorAll("[data-session-id]").forEach((button) => {
    button.addEventListener("click", async () => callbacks.openSession(button.dataset.sessionId));
  });
}

export function renderConversation() {
  refreshConversationHeading("已加载这条会话的历史聊天记录");
  elements.contextMessageCount.textContent = String(state.messages.length);
  elements.contextSummaryState.textContent = state.summary ? "已压缩" : "无";
  renderContextObservation();
  updateWelcomeStrip();

  if (state.summary?.summary_text) {
    elements.summaryBanner.hidden = false;
    elements.summaryText.textContent = state.summary.summary_text;
  } else {
    elements.summaryBanner.hidden = true;
    elements.summaryText.textContent = "";
  }

  if (!state.messages.length) {
    renderEmptyConversation("当前 session 还没有历史消息，直接发第一条就行。");
    return;
  }
  const latestAssistantIndex = findLatestAssistantIndex(state.messages);
  elements.chatLog.innerHTML = state.messages
    .map((item, index) => renderMessageHtml(item, index, index === latestAssistantIndex))
    .join("");
  scrollChatToBottom();
}

export function renderEmptyConversation(message = "当前还没有消息。直接输入问题，系统会把后续对话保存进这个 session。") {
  refreshConversationHeading("已准备好开始聊天");
  elements.contextMessageCount.textContent = "0";
  elements.contextSummaryState.textContent = "无";
  state.contextObservation = null;
  renderContextObservation();
  updateWelcomeStrip();
  elements.summaryBanner.hidden = true;
  elements.summaryText.textContent = "";
  elements.chatLog.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
}

export function appendMessage(message) {
  if (!state.messages.length && elements.chatLog.querySelector(".empty-state")) {
    elements.chatLog.innerHTML = "";
  }
  state.messages.push(message);
  elements.contextMessageCount.textContent = String(state.messages.length);
  elements.chatLog.querySelectorAll(".message-followups").forEach((item) => item.remove());
  elements.chatLog.insertAdjacentHTML(
    "beforeend",
    renderMessageHtml(message, state.messages.length - 1, message.role === "assistant")
  );
  updateWelcomeStrip();
  scrollChatToBottom();
}

export function renderStreamingText(text, placeholder = "") {
  const content = String(text || "");
  if (!content.trim()) {
    return `
      <div class="thinking-line">
        <span></span><span></span><span></span>
        <strong>${escapeHtml(placeholder || "思考中...")}</strong>
      </div>
    `;
  }
  return renderMarkdown(content);
}

export function renderContextObservation() {
  const budget = state.contextObservation?.budget || null;
  const compression = state.contextObservation?.compression || null;
  if (!budget) {
    elements.contextBudgetUsage.textContent = "未知";
    elements.contextTruncationState.textContent = "未知";
  } else {
    const used = Number(budget.used_value || 0);
    const limit = budget.limit_value == null ? "无限制" : `${budget.limit_value}`;
    elements.contextBudgetUsage.textContent = `${used}/${limit}`;
    elements.contextTruncationState.textContent = budget.truncated ? "已截断" : "完整";
  }

  if (!compression) {
    elements.contextCompressionSource.textContent = "未知";
    elements.contextCompressionCount.textContent = "0";
    return;
  }
  elements.contextCompressionSource.textContent = compression.summary_present
    ? compression.summary_source === "stored" ? "已持久化摘要" : "现场压缩"
    : "无摘要";
  elements.contextCompressionCount.textContent = String(compression.summarized_message_count || 0);
}

export function refreshConversationHeading(description) {
  const currentSession = state.sessions.find((item) => item.session_id === state.sessionId);
  const title = currentSession?.title || state.sessionId || "新对话";
  const subtitle = description || (state.messages.length ? "已加载历史聊天记录" : "已准备好开始聊天");
  elements.chatSessionTitle.textContent = title;
  elements.chatSessionMeta.textContent = `${state.sessionId || "未命名 session"} · ${subtitle}`;
}

export function updateWelcomeStrip() {
  elements.welcomeStrip.hidden = state.messages.length > 0 || Boolean(state.activeRun);
}

export function renderSchedules(items, callbacks) {
  const activeCount = items.filter((item) => item.status === "enabled").length;
  const pausedCount = items.filter((item) => item.status === "paused").length;
  elements.scheduleTotalCount.textContent = String(items.length);
  elements.scheduleActiveCount.textContent = String(activeCount);
  elements.schedulePausedCount.textContent = String(pausedCount);
  elements.scheduleCountLabel.textContent = `${items.length} 个任务`;
  if (!items.length) {
    elements.scheduleList.innerHTML = `
      <div class="schedule-empty-state">
        <span class="schedule-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="m9 16 2 2 4-5"/></svg></span>
        <strong>暂无自动任务</strong>
        <p>创建后的任务会在这里统一展示和管理。</p>
      </div>`;
    return;
  }
  elements.scheduleList.innerHTML = items.map((item) => {
    const status = scheduleStatusMeta(item.status);
    return `
      <article class="schedule-task-card">
        <header class="schedule-task-head">
          <div class="schedule-task-title">
            <span>任务 #${escapeHtml(item.id)}</span>
            <h3>${escapeHtml(item.title || "未命名任务")}</h3>
          </div>
          <span class="schedule-status ${status.className}"><span aria-hidden="true">${status.icon}</span>${status.label}</span>
        </header>
        <dl class="schedule-task-meta">
          <div><dt>执行周期</dt><dd>每天 ${escapeHtml(item.schedule_time || "--:--")}</dd></div>
          <div><dt>时区</dt><dd>${escapeHtml(item.timezone || "未设置")}</dd></div>
          <div class="schedule-next-run"><dt>下次运行</dt><dd>${formatDateTime(item.next_run_at)}</dd></div>
        </dl>
        <div class="schedule-task-actions">
          <button class="ghost-button compact-button" data-action="run-now" data-job-id="${item.id}">立即执行</button>
          <button class="ghost-button compact-button" data-action="${item.status === "paused" ? "resume" : "pause"}" data-job-id="${item.id}">
            ${item.status === "paused" ? "恢复任务" : "暂停任务"}
          </button>
          <button class="text-button schedule-history-button" data-action="show-runs" data-job-id="${item.id}">
            查看记录
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
          </button>
        </div>
      </article>`;
  }).join("");

  elements.scheduleList.querySelectorAll("button[data-action]").forEach((button) => {
    button.addEventListener("click", async () => callbacks.handleScheduleAction(button.dataset.action, Number(button.dataset.jobId)));
  });
}

function scheduleStatusMeta(status) {
  if (status === "enabled") return { label: "已启用", className: "is-active", icon: "✓" };
  if (status === "paused") return { label: "已暂停", className: "is-paused", icon: "Ⅱ" };
  if (status === "error") return { label: "异常", className: "is-error", icon: "!" };
  return { label: "未知状态", className: "is-muted", icon: "–" };
}

export function renderRuns(items) {
  if (!items.length) {
    elements.runsTable.innerHTML = `<tr><td colspan="5">暂无执行记录。</td></tr>`;
    return;
  }
  elements.runsTable.innerHTML = items.map((item) => `
    <tr>
      <td>${item.id}</td>
      <td class="${statusClass(item.status)}">${escapeHtml(item.status)}</td>
      <td>${formatDateTime(item.started_at)}</td>
      <td>${formatDateTime(item.finished_at)}</td>
      <td>${escapeHtml(compactText(item.output_text || item.error_message || "无", 120))}</td>
    </tr>
  `).join("");
}

export function renderMessageHtml(message, index = 0, showFollowups = false) {
  const role = message.role === "user" ? "user" : "assistant";
  const label = role === "user" ? "你" : "Agent";
  const retryAction = role === "user"
    ? `<button type="button" data-message-action="retry" data-message-index="${index}">重试</button>`
    : "";
  const downloadAction = role === "assistant"
    ? `<button type="button" data-message-action="download" data-message-index="${index}">下载 .md</button>`
    : "";
  const followupsHtml = role === "assistant" && showFollowups
    ? renderFollowupActions(message.content || "", message.run_id)
    : "";
  const runSummaryHtml = role === "assistant" && message.run_id
    ? renderRunSummary(message.run_id)
    : "";
  return `
    <article class="message-row ${role}" data-message-index="${index}">
      <div class="message-content">
        <div class="message-meta">
          <strong>${label}</strong>
          <span>${formatClock(message.created_at)}</span>
          <div class="message-actions" aria-label="消息操作">
            <button type="button" data-message-action="copy" data-message-index="${index}">复制</button>
            <button type="button" data-message-action="reuse" data-message-index="${index}">放入输入框</button>
            ${downloadAction}
            ${retryAction}
          </div>
        </div>
        <div class="message-body">${renderMarkdown(message.content || "")}</div>
        ${runSummaryHtml}
        ${followupsHtml}
      </div>
    </article>
  `;
}

function renderRunSummary(runId) {
  return `
    <div class="message-run-summary">
      <span><i aria-hidden="true"></i>已完成</span>
      <button type="button" data-run-id="${escapeHtml(runId)}">查看过程</button>
    </div>
  `;
}

function renderFollowupActions(content) {
  const suggestions = buildFollowupSuggestions(content);
  if (!suggestions.length) {
    return "";
  }
  return `
    <div class="message-followups" aria-label="建议追问">
      <span>${suggestions.length ? "下一步" : "运行"}</span>
      ${suggestions.map((item) => `
        <button type="button" data-followup-prompt="${escapeHtml(item.prompt)}">${escapeHtml(item.label)}</button>
      `).join("")}
    </div>
  `;
}

function buildFollowupSuggestions(content) {
  const text = String(content || "").trim();
  if (!text || isSimpleAssistantReply(text)) {
    return [];
  }
  if (/请求失败|执行失败|已停止|错误/.test(text)) {
    return [
      { label: "定位原因", prompt: "请根据刚才的失败信息，帮我定位原因并给出修复步骤" },
      { label: "换法重试", prompt: "请换一种更稳妥的方式继续完成刚才的任务" },
    ];
  }
  if (/日报|提交|commits?|git/i.test(text)) {
    return [
      { label: "提炼重点", prompt: "把刚才的日报内容压缩成 3 条最重要的工作成果" },
      { label: "补明日计划", prompt: "基于刚才的日报，补充风险点和明日计划" },
    ];
  }
  return [
    { label: "继续深入", prompt: "继续展开刚才的内容，补充关键细节和判断依据" },
    { label: "生成计划", prompt: "基于刚才的回答，给我一个下一步执行计划" },
  ];
}

function filterSessions(sessions, query) {
  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!tokens.length) {
    return sessions;
  }
  return sessions.filter((item) => {
    const haystack = [item.title, item.session_id, item.message_count, item.last_message_at]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return tokens.every((token) => haystack.includes(token));
  });
}

function filterSkills(skills, query) {
  const tokens = String(query || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!tokens.length) {
    return skills;
  }
  return skills.filter((item) => {
    const haystack = [
      item.name,
      item.description,
      item.status,
      item.source,
      item.risk_level,
      item.compatibility?.status,
      item.compatibility?.reason,
      ...(item.tools || []),
      ...(item.capabilities || []),
      ...(item.triggers || []),
      ...(item.intent_examples || []),
      item.path,
      item.install_source,
    ].filter(Boolean).join(" ").toLowerCase();
    return tokens.every((token) => haystack.includes(token));
  });
}

function isSimpleAssistantReply(text) {
  if (text.length > 80) return false;
  return /^(你好|您好|我在|收到|好的|可以|没问题)[，,。！!\s]/.test(text);
}

function statusClass(status) {
  if (status === "success" || status === "enabled") return "status-success";
  if (status === "error") return "status-error";
  if (status === "paused") return "status-paused";
  return "";
}

function skillStatusText(status) {
  if (status === "enabled") return "启用中";
  if (status === "disabled") return "已停用";
  if (status === "error") return "异常";
  return "可用";
}

function skillBadgeText(item) {
  const status = skillStatusText(item.status);
  const compatibility = skillCompatibilityText(item.compatibility?.status);
  return compatibility ? `${status} · ${compatibility}` : status;
}

function skillCompatibilityText(status) {
  if (status === "native") return "原生可执行";
  if (status === "compatible") return "兼容可执行";
  if (status === "document_only") return "仅文档";
  if (status === "unsupported") return "不兼容";
  if (status === "error") return "解析异常";
  return "未知";
}

function skillRiskText(value) {
  if (value === "high") return "高";
  if (value === "medium") return "中";
  if (value === "low") return "低";
  return value || "未知";
}

function skillSourceText(source) {
  if (source === "builtin") return "内置";
  if (source === "workspace") return "工作区目录";
  if (source === "imported") return "本地导入";
  return source || "未知";
}

export function scrollChatToBottom() {
  scrollChatToLatest({ force: true });
}

function findLatestAssistantIndex(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === "assistant") return index;
  }
  return -1;
}
