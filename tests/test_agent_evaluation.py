from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from daily_agent.config import Settings
from daily_agent.mcp_catalog import McpServerRecord
from daily_agent.model_provider import ModelConnectionTester
from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_router import LocalSkillSelector, SkillRouter
from daily_agent.skill_slot_filling import SkillSlotFiller
from daily_agent.skill_tools import _make_entrypoint, build_skill_tools
from daily_agent.tool_registry import ToolRegistry

EVAL_PATH = Path(__file__).resolve().parents[1] / "evals" / "agent_cases.json"


class _CatalogStub:
    def __init__(self, items=None):
        self.items = list(items or [])

    def list_skills(self):
        return list(self.items)

    def list_servers(self):
        return list(self.items)


class _DisabledToolRegistry:
    def is_tool_enabled(self, tool_id):
        return False


def _skill(name: str) -> SkillRecord:
    if name == "daily-report":
        return SkillRecord(
            name=name,
            description="根据 git 提交记录生成工作日报。",
            status="enabled",
            source="workspace",
            path="/tmp/daily-report",
            tools=("daily_report",),
            tool_name="daily_report",
            triggers=("日报", "工作总结"),
            intent_examples=("帮我生成今天的日报",),
            input_schema={
                "type": "object",
                "properties": {"repos": {"type": "array", "title": "仓库路径"}},
                "required": ["repos"],
            },
            execution={"type": "python_callable", "entrypoint": "demo:run"},
            interaction_mode="react_tool",
        )
    return SkillRecord(
        name="project-inspector",
        description="检查当前项目结构、主要入口和关键流程。",
        status="enabled",
        source="workspace",
        path="/tmp/project-inspector",
        tools=("project_inspector",),
        tool_name="project_inspector",
        triggers=("项目结构", "当前项目"),
        intent_examples=("检查当前项目结构",),
        input_schema={"type": "object", "properties": {}},
        execution={"type": "python_callable", "entrypoint": "demo:run"},
        interaction_mode="react_tool",
    )


def _settings(api_key: str) -> Settings:
    return Settings(
        model_provider="openai_compatible",
        model_id="deepseek-chat",
        model_api_key=api_key,
        model_base_url="https://model.example/v1",
        agno_telemetry=False,
        context_token_budget=4000,
        mysql_host="127.0.0.1",
        mysql_port=3306,
        mysql_user="root",
        mysql_password="password",
        mysql_database="sb_agent",
        agent_max_tool_calls=16,
        agent_max_duration_seconds=180,
    )


def _load_cases() -> list[dict]:
    return json.loads(EVAL_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
def test_fixed_agent_evaluation(case, tmp_path):
    # 每种 case 都只验证确定性契约，不调用付费模型，所以这组评估可以放进每次全量测试。
    if case["type"] == "route":
        skills = [_skill("daily-report"), _skill("project-inspector")]
        router = SkillRouter(selector=LocalSkillSelector())
        decision = router.route(query=case["query"], candidates=skills)
        assert (decision.skill_name if decision else None) == case["expected_skill"]
        return

    if case["type"] == "missing_fields":
        result = SkillSlotFiller().fill(skill=_skill(case["skill"]), query=case["query"])
        assert result.missing_info == case["expected_missing"]
        return

    if case["type"] == "model_health":
        # 没有 Key 时连接测试必须在发网络请求前失败，避免出现含糊的外部异常。
        result = ModelConnectionTester(probe=lambda settings, timeout: None).check(_settings(case["api_key"]))
        assert result.status == case["expected_status"]
        return

    if case["type"] == "tool_failure":
        runtime = SimpleNamespace(
            run=lambda **kwargs: SimpleNamespace(status="error", response_text="工具执行失败", output_text=None)
        )
        entrypoint = _make_entrypoint(
            _skill(case["skill"]), runtime, user_id="eval", session_id="eval", run_id="eval-run"
        )
        with pytest.raises(RuntimeError):
            entrypoint()
        return

    if case["type"] == "disabled_skill":
        tools = build_skill_tools(
            user_id="eval",
            session_id="eval",
            skill_records=[_skill(case["skill"])],
            runtime=SimpleNamespace(),
            tool_registry=_DisabledToolRegistry(),
        )
        assert len(tools) == case["expected_tool_count"]
        return

    if case["type"] == "mcp_visibility":
        server = McpServerRecord(
            server_id="mcp-eval",
            name="Eval MCP",
            transport_type="stdio",
            tools=[{"name": "echo", "description": "回显"}],
        )
        registry = ToolRegistry(
            path=tmp_path / "tools.json",
            skill_catalog=_CatalogStub(),
            mcp_catalog=_CatalogStub([server]),
        )
        connected = {server.server_id} if case["connected"] else set()
        assert registry.list_tools(connected_server_ids=connected)[0].available is case["expected_available"]
        return

    raise AssertionError(f"未知评估类型：{case['type']}")
