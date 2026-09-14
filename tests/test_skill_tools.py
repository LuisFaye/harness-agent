from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_tools import _build_tool_description, _make_entrypoint, build_skill_tools


class _EnabledToolRegistry:
    def is_tool_enabled(self, tool_id):
        return True


def test_skill_tool_entrypoint_uses_task_as_query(monkeypatch):
    calls = []

    class FakeRuntime:
        def run(self, **kwargs):
            calls.append(kwargs)
            return type("R", (), {"output_text": "done", "response_text": "done"})()

    skill = SkillRecord(
        name="demo",
        description="demo",
        status="enabled",
        source="imported",
        path="/tmp/demo",
        tools=("demo",),
        tool_name="demo",
        input_schema={"type": "object", "properties": {"task": {"type": "string"}}},
    )

    entrypoint = _make_entrypoint(skill, FakeRuntime(), user_id="u1", session_id="s1")
    entrypoint(task="帮我做事")

    assert calls[0]["query"] == "帮我做事"


def test_skill_tool_entrypoint_raises_on_skill_error():
    class FakeRuntime:
        def run(self, **kwargs):
            return type("R", (), {"status": "error", "response_text": "boom", "output_text": None})()

    skill = SkillRecord(
        name="demo",
        description="demo",
        status="enabled",
        source="imported",
        path="/tmp/demo",
        tools=("demo",),
        tool_name="demo",
        input_schema={"type": "object", "properties": {}},
    )

    entrypoint = _make_entrypoint(skill, FakeRuntime(), user_id="u1", session_id="s1")

    try:
        entrypoint()
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("技能失败时应该抛出异常，让工具调用标记为失败")


def test_compatible_skill_tool_description_tells_model_to_pass_command():
    skill = SkillRecord(
        name="core",
        description="浏览器操作",
        status="enabled",
        source="imported",
        path="/tmp/core",
        tools=("core",),
        tool_name="core",
        execution={"type": "compatible_skill"},
    )

    description = _build_tool_description(skill)

    assert "inputs.command" in description
    assert "agent-browser open" in description


def test_confirmed_tool_does_not_request_confirmation_again():
    skill = SkillRecord(
        name="browser",
        description="浏览器操作",
        status="enabled",
        source="imported",
        path="/tmp/browser",
        tools=("agent_browser",),
        tool_name="agent_browser",
        interaction_mode="react_tool",
        requires_confirmation=True,
        execution={"type": "compatible_skill"},
    )

    tools = build_skill_tools(
        user_id="u1",
        session_id="s1",
        skill_records=[skill],
        runtime=object(),
        tool_registry=_EnabledToolRegistry(),
        confirmed_tool_names={"agent_browser"},
    )

    assert len(tools) == 1
    assert tools[0].requires_confirmation is False
