from datetime import date

from daily_agent.executor import TaskExecutor, extract_dates
from daily_agent.planner import PlanStep, RuleBasedPlanner, TaskPlan


def test_executor_general_plan_does_not_call_fixed_domain_tools():
    executor = TaskExecutor(response_renderer=_fake_renderer)
    plan = RuleBasedPlanner().plan("查一下某个外部数据")

    result = executor.run("查一下某个外部数据", user_id="u1", plan=plan)

    assert result.status == "success"
    assert result.observations == []
    assert result.response_text == "最终回答"


def test_executor_reports_readable_error_for_unregistered_tool():
    plan = TaskPlan(
        intent="skill:missing",
        steps=[
            PlanStep(
                name="执行缺失工具",
                tool="missing_tool",
                reason="测试未启用工具时的兜底说明。",
            )
        ],
        missing_info=[],
        risk_level="low",
    )
    executor = TaskExecutor(tool_registry={}, response_renderer=_fake_renderer)

    result = executor.run("执行缺失工具", user_id="u1", plan=plan)

    assert result.status == "needs_input"
    assert result.missing_info == ["未知工具：missing_tool"]
    assert "请直接把这些信息告诉我" in result.response_text


def test_executor_runs_explicitly_registered_tool():
    calls = []

    def fake_tool(**kwargs):
        calls.append(kwargs)
        return '{"status":"ok"}'

    def resolve_inputs(query, user_id):
        return {"query": query, "user_id": user_id}, []

    executor = TaskExecutor(
        tool_registry={"custom_tool": fake_tool},
        response_renderer=_fake_renderer,
    )
    executor.tool_input_resolvers = {"custom_tool": resolve_inputs}
    plan = TaskPlan(
        intent="skill:custom",
        steps=[PlanStep(name="执行自定义工具", tool="custom_tool", reason="测试显式注册工具。")],
        missing_info=[],
        risk_level="low",
    )

    result = executor.run("测试", user_id="u1", plan=plan)

    assert result.status == "success"
    assert [item.tool for item in result.observations] == ["custom_tool"]
    assert calls == [{"query": "测试", "user_id": "u1"}]
    assert result.response_text == "最终回答"


def test_extract_dates_handles_relative_weekday():
    assert extract_dates("下周五出发", today=date(2026, 5, 21)) == ["2026-05-29"]


def test_render_response_strips_internal_panel_dump(monkeypatch):
    class FakeRunOutput:
        def get_content_as_string(self):
            return (
                "│ │ 请根据下面已经完成的执行结果，给用户一个简洁、可执行的中文回答。 │ │ "
                "用户原始问题：你好 │ │ 任务计划： │ │ - 识别意图：general │ │ "
                "没有调用实时工具，请直接回答用户问题。 │ │ Response (4.2s) │ │ "
                "你好！我是 SB Agent。 │ │"
            )

    class FakeAgent:
        def run(self, prompt, **kwargs):
            return FakeRunOutput()

    monkeypatch.setattr("daily_agent.executor.build_agent", lambda **kwargs: FakeAgent())

    executor = TaskExecutor()
    plan = RuleBasedPlanner().plan("你好")
    result = executor.run("你好", user_id="u1", session_id="work", plan=plan)

    assert result.status == "success"
    assert result.response_text == "你好！我是 SB Agent。"


def _fake_renderer(query, plan, observations, user_id, session_id):
    return "最终回答"
