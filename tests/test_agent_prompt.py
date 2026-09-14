from datetime import datetime
from types import SimpleNamespace

from daily_agent.agent import BASE_INSTRUCTIONS, build_agent, build_prompt_snapshot
from daily_agent.agent import _build_runtime_context_instructions, _build_skill_loaders
from daily_agent.skill_catalog import SkillRecord


def test_base_instructions_are_generic():
    text = "\n".join(BASE_INSTRUCTIONS).lower()

    assert "通用 agent" in text.lower()
    assert "search_flights" not in text
    assert "bitcoin" not in text
    assert "btc" not in text
    assert "mcp resource" in text
    assert "外部内容" in text
    assert "下载 .md" in text
    assert "不得声称没有发送" in text


def test_prompt_snapshot_lists_skill_tools_only(monkeypatch):
    monkeypatch.setattr(
        "daily_agent.agent.list_enabled_skill_records",
        lambda: [
            SkillRecord(
                name="daily-report",
                description="生成日报",
                status="enabled",
                source="workspace",
                path="/tmp/daily-report",
                tools=(),
                tool_name="daily_report",
            )
        ],
    )
    monkeypatch.setattr("daily_agent.agent._build_context_instructions", lambda **kwargs: "")
    monkeypatch.setattr("daily_agent.agent._skill_tool_names", lambda skills: ["daily_report"])

    snapshot = build_prompt_snapshot("生成日报")

    assert snapshot.tool_names == ["daily_report", "database_connect"]
    assert snapshot.skill_names == ["daily-report"]


def test_prompt_snapshot_includes_extra_context(monkeypatch):
    monkeypatch.setattr("daily_agent.agent.list_enabled_skill_records", lambda: [])
    monkeypatch.setattr("daily_agent.agent._build_context_instructions", lambda **kwargs: "")

    snapshot = build_prompt_snapshot("A", extra_context_instructions="临时会话上下文：上一轮让用户选择 A/B")

    assert "临时会话上下文：上一轮让用户选择 A/B" in snapshot.prompt_text


def test_runtime_context_includes_current_date():
    text = _build_runtime_context_instructions(datetime(2026, 5, 29, 9, 30, 0))

    assert "当前日期：2026-05-29" in text
    assert "当前星期：星期五" in text
    assert "Asia/Shanghai" in text


def test_skill_loaders_only_load_document_skills(monkeypatch):
    calls = []

    class LocalSkillsSpy:
        def __init__(self, path, validate=True):
            calls.append({"path": path, "validate": validate})

    monkeypatch.setattr(
        "daily_agent.agent.list_enabled_skill_records",
        lambda: [
            SkillRecord(
                name="knowledge-only",
                description="知识说明",
                status="enabled",
                source="imported",
                path="/tmp/knowledge-only",
                tools=(),
            ),
            SkillRecord(
                name="executable",
                description="可执行能力",
                status="enabled",
                source="workspace",
                path="/tmp/executable",
                tools=("executable",),
                tool_name="executable",
                execution={"type": "python_callable", "entrypoint": "example:run"},
                interaction_mode="react_tool",
            ),
        ],
    )

    _build_skill_loaders(LocalSkillsSpy)

    assert calls == [{"path": "/tmp/knowledge-only", "validate": False}]


def test_prompt_snapshot_excludes_skill_with_disabled_tool(monkeypatch):
    monkeypatch.setattr(
        "daily_agent.agent.list_enabled_skill_records",
        lambda: [
            SkillRecord(
                name="daily-report",
                description="生成日报",
                status="enabled",
                source="workspace",
                path="/tmp/daily-report",
                tools=("daily_report",),
                tool_name="daily_report",
                execution={"type": "python_callable", "entrypoint": "example:run"},
                interaction_mode="react_tool",
            )
        ],
    )
    monkeypatch.setattr("daily_agent.agent._build_context_instructions", lambda **kwargs: "")
    monkeypatch.setattr("daily_agent.agent._skill_tool_names", lambda skills: [])

    snapshot = build_prompt_snapshot("你能做什么")

    assert snapshot.tool_names == ["database_connect"]
    assert snapshot.skill_names == []
    assert "daily-report" not in "\n".join(snapshot.instructions)


def test_build_agent_passes_explicit_telemetry_setting(monkeypatch):
    captured = {}

    class AgentSpy:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class SkillsStub:
        def __init__(self, loaders):
            self.loaders = loaders

    monkeypatch.setattr("agno.agent.Agent", AgentSpy)
    monkeypatch.setattr("agno.skills.Skills", SkillsStub)
    monkeypatch.setattr(
        "daily_agent.agent.load_settings",
        lambda: SimpleNamespace(model_api_key="sk-test", agno_telemetry=False),
    )
    monkeypatch.setattr("daily_agent.agent.build_model", lambda settings: "model")
    monkeypatch.setattr("daily_agent.agent.build_prompt_snapshot", lambda **kwargs: type("Snapshot", (), {"instructions": []})())
    monkeypatch.setattr("daily_agent.agent.build_skill_tools", lambda **kwargs: [])
    monkeypatch.setattr("daily_agent.agent._connected_mcp_tools", lambda: [])
    monkeypatch.setattr("daily_agent.agent._build_skill_loaders", lambda loader: [])

    build_agent(enable_tools=False)

    assert captured["telemetry"] is False
    assert captured["tool_call_limit"] == 8


def test_build_agent_passes_custom_tool_call_limit(monkeypatch):
    captured = {}

    class AgentSpy:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class SkillsStub:
        def __init__(self, loaders):
            self.loaders = loaders

    monkeypatch.setattr("agno.agent.Agent", AgentSpy)
    monkeypatch.setattr("agno.skills.Skills", SkillsStub)
    monkeypatch.setattr(
        "daily_agent.agent.load_settings",
        lambda: SimpleNamespace(model_api_key="sk-test", agno_telemetry=False),
    )
    monkeypatch.setattr("daily_agent.agent.build_model", lambda settings: "model")
    monkeypatch.setattr("daily_agent.agent.build_prompt_snapshot", lambda **kwargs: type("Snapshot", (), {"instructions": []})())
    monkeypatch.setattr("daily_agent.agent.build_skill_tools", lambda **kwargs: [])
    monkeypatch.setattr("daily_agent.agent._connected_mcp_tools", lambda: [])
    monkeypatch.setattr("daily_agent.agent._build_skill_loaders", lambda loader: [])

    hook = lambda **kwargs: None
    build_agent(enable_tools=False, tool_call_limit=3, tool_hooks=[hook])

    assert captured["tool_call_limit"] == 3
    assert captured["tool_hooks"] == [hook]
