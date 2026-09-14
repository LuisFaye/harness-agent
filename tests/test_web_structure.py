from pathlib import Path


WEB_ROOT = Path(__file__).resolve().parents[1] / "src" / "daily_agent" / "web"


def _section(html: str, section_id: str, next_section_id: str) -> str:
    start = html.index(f'id="{section_id}"')
    end = html.index(f'id="{next_section_id}"', start)
    return html[start:end]


def test_agent_page_keeps_developer_panels_out_of_primary_chat():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    chat = _section(html, "view-chat", "view-schedule")

    assert "实时展示执行过程" not in chat
    assert "execution-timeline" not in chat
    assert "open-developer-panel" not in chat
    assert "open-prompt-panel" not in chat
    assert "chat-log" in chat
    assert "composer-status-text" in chat
    assert 'class="activity-bar"' not in chat
    assert 'id="view-active-run-details"' not in chat


def test_global_sidebar_no_longer_mixes_sessions_or_skill_inventory():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    sidebar = html[html.index('<aside class="sidebar"'):html.index('<main class="workspace"')]
    chat = _section(html, "view-chat", "view-schedule")

    assert 'id="session-list"' not in sidebar
    assert 'id="skills-list"' not in sidebar
    assert 'class="agent-workbench-layout"' in chat
    assert 'class="agent-session-panel"' in chat
    assert 'id="session-list"' in chat


def test_run_monitor_has_filters_list_detail_and_legacy_schedule_runs():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    runs = _section(html, "view-runs", "skills-modal")

    assert 'id="run-filter-form"' in runs
    assert 'id="agent-run-list"' in runs
    assert 'id="agent-run-detail"' in runs
    assert 'class="automatic-run-panel"' in runs


def test_agent_answer_summary_opens_lazy_run_drawer():
    source = (WEB_ROOT / "app" / "trace.js").read_text(encoding="utf-8")
    render_start = source.index("function renderActiveRunHtml")
    render_end = source.index("function setOptionalText", render_start)
    renderer = source[render_start:render_end]

    assert "data-run-id" in renderer
    assert "renderProcessPanel" not in renderer
    assert "run-activity-stream" not in renderer
    assert "data-run-action" not in renderer

    main_source = (WEB_ROOT / "app" / "main.js").read_text(encoding="utf-8")
    handler = main_source[main_source.index("async function handleMessageAction"):]
    assert "openPersistedRunDrawer(runId)" in handler
    assert "/runs/${encodeURIComponent(runId)}" in handler
    assert "renderPersistedRunDrawer(detail)" in handler


def test_run_drawer_is_collapsible_and_loads_history_by_run_id():
    process_source = (WEB_ROOT / "app" / "process-view.js").read_text(encoding="utf-8")
    render_source = (WEB_ROOT / "app" / "render.js").read_text(encoding="utf-8")
    main_source = (WEB_ROOT / "app" / "main.js").read_text(encoding="utf-8")
    drawer_source = (WEB_ROOT / "app" / "run-drawer.js").read_text(encoding="utf-8")

    assert "<details" in process_source
    assert "aria-live" in process_source and "polite" in process_source
    assert "renderHistoricalProcessPanel(message.run_id)" not in render_source
    assert 'addEventListener("toggle", handleProcessToggle, true)' not in main_source
    assert "openPersistedRunDrawer" in drawer_source
    assert "renderLoadedProcessPanel" in drawer_source
    assert "returnFocus" in drawer_source


def test_live_process_uses_compact_tree_instead_of_nested_cards():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    main_source = (WEB_ROOT / "app" / "main.js").read_text(encoding="utf-8")
    process_source = (WEB_ROOT / "app" / "process-view.js").read_text(encoding="utf-8")
    trace_source = (WEB_ROOT / "app" / "trace.js").read_text(encoding="utf-8")
    styles_source = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")
    process_styles = (WEB_ROOT / "styles" / "execution-process.css").read_text(encoding="utf-8")

    drawer_source = (WEB_ROOT / "app" / "run-drawer.js").read_text(encoding="utf-8")

    assert "实时展示执行过程" not in html
    assert 'id="composer-activity"' not in html
    assert 'class="activity-bar"' not in html
    assert 'class="run-drawer"' in html
    assert "execution-stream" in process_source
    assert "execution-process.css?v=4" in styles_source
    assert ".message-process .execution-step-row" in process_styles
    assert "display: grid" in process_styles
    assert 'class="process-timeline-step"' in process_source
    assert "execution-tree" in process_source
    assert "execution-children" in process_source
    assert "capability-badge" in process_source
    assert "tool-call-card" not in process_source
    assert 'capabilityType: "skill"' in process_source
    assert 'mcp ? "mcp" : "tool"' in process_source
    assert 'capabilityType: "subagent"' in process_source
    assert "items.slice(-10)" not in process_source
    assert "renderProcessPanel" not in trace_source
    assert "forceOpen: true" in drawer_source
    assert 'event.target.closest("[data-active-run-action]")' in main_source
    assert "elements.attachFileButton?.addEventListener" in main_source
    assert "if (!elements.attachmentTray) return" in main_source
    assert "if (state.activeRun) return;" in main_source
    assert 'if (state.activeRun) return showToast("Agent 回复期间不会自动改写输入框。", true);' in main_source
    assert 'autocomplete="off"' in html


def test_web_assets_disable_cache_to_keep_module_and_style_versions_consistent():
    api_source = (WEB_ROOT.parent / "api.py").read_text(encoding="utf-8")
    assets_source = (WEB_ROOT.parent / "web_assets.py").read_text(encoding="utf-8")

    assert "class NoCacheStaticFiles" in assets_source
    assert 'STATIC_CACHE_CONTROL = "no-store, max-age=0"' in assets_source
    assert "mount_workbench_frontends(app, WEB_DIR, NEXT_WEB_DIR)" in api_source
    assert 'NoCacheStaticFiles(directory=legacy_directory)' in assets_source
    assert 'headers={"Cache-Control": STATIC_CACHE_CONTROL}' in assets_source
    assert "class ReactStaticFiles" in assets_source
    assert 'HASHED_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"' in assets_source
    assert 'ReactStaticFiles(directory=react_directory, html=True)' in assets_source


def test_active_run_renders_markdown_while_streaming_and_after_completion():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    trace_source = (WEB_ROOT / "app" / "trace.js").read_text(encoding="utf-8")
    stream_source = (WEB_ROOT / "app" / "stream-render.js").read_text(encoding="utf-8")

    assert '<details class="summary-banner"' in html
    assert "elements.summaryBanner.hidden = true" in trace_source
    assert "const revealResponse = Boolean(run.streamedText)" in trace_source
    assert 'body.classList.toggle("is-streaming", hasContent && !run.responseCompleted)' in stream_source
    assert 'body.innerHTML = renderStreamingText(run.streamedText, "")' in stream_source
    assert 'const responseContent = renderStreamingText(run.streamedText, "")' in trace_source


def test_chat_supports_session_attachments_and_answer_download():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    api_source = (WEB_ROOT / "app" / "api.js").read_text(encoding="utf-8")
    main_source = (WEB_ROOT / "app" / "main.js").read_text(encoding="utf-8")
    render_source = (WEB_ROOT / "app" / "render.js").read_text(encoding="utf-8")

    assert 'id="attachment-input"' in html
    assert 'id="attach-file-button"' in html
    assert 'id="attachment-tray"' in html
    assert "attachment_ids" in api_source
    assert "await loadAttachmentsForCurrentSession()" in main_source
    assert 'data-message-action="download"' in render_source
    assert "下载 .md" in render_source
    assert "data-active-message-action" in (WEB_ROOT / "app" / "trace.js").read_text(encoding="utf-8")


def test_model_settings_use_generic_provider_and_do_not_refill_api_key():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    source = (WEB_ROOT / "app" / "main.js").read_text(encoding="utf-8")

    assert 'id="model-provider"' in html
    assert 'id="test-model-connection"' in html
    assert "不要直接暴露到公网" in html
    assert "dashscope_api_key" not in source
    assert 'elements.modelApiKey.value = ""' in source


def test_skill_manager_can_inspect_safe_internal_files():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    main_source = (WEB_ROOT / "app" / "main.js").read_text(encoding="utf-8")
    render_source = (WEB_ROOT / "app" / "render.js").read_text(encoding="utf-8")
    capability_source = (WEB_ROOT / "app" / "capability-render.js").read_text(encoding="utf-8")

    assert "SKILL.md、内部脚本和实际持久化位置" in html
    assert 'data-skill-inspect="${escapeHtml(item.name)}"' in capability_source
    assert '/detail`)' in main_source
    assert 'openModal(elements.skillsModal)' in main_source
    assert 'class="skill-file"' in render_source
    assert 'class="skill-file-content"' in render_source
    assert "status_registry_path" in render_source
