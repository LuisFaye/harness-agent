from datetime import datetime

from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.requirement import RunRequirement
from agno.run.base import RunStatus

from daily_agent.config import Settings
from daily_agent.errors import MissingConfigError
from daily_agent.react_runner import AgentLoopRunner
from daily_agent.react_runner import _format_relative_date_answer
from daily_agent.react_runner import ReactPauseStore
from daily_agent.services.agent_service import AskService
from daily_agent.executor import ExecutionResult
from daily_agent.planner import TaskPlan
from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_runtime import SkillRunResult


class _Event:
    def __init__(self, event, content=None, **kwargs):
        self.event = event
        self.content = content
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_react_runner_handles_simple_greeting_locally():
    result = AgentLoopRunner(agent_factory=_failing_agent_factory).run("你好")

    assert result.status == "success"
    assert result.response_text == "你好，我在。你可以直接告诉我要处理什么任务。"
    assert result.observations == []
    assert result.stop_reason == "本地处理简单请求"


def test_react_runner_answers_simple_date_locally():
    result = AgentLoopRunner(agent_factory=_failing_agent_factory).run("今天是几号")

    assert result.status == "success"
    assert "模型没有返回有效内容" not in result.response_text
    assert result.response_text.startswith("今天是 ")


def test_format_relative_date_answer_uses_asia_shanghai_date():
    now = datetime(2026, 5, 29, 10, 0, 0)

    assert _format_relative_date_answer(0, now=now) == "今天是 2026-05-29，星期五。"
    assert _format_relative_date_answer(1, now=now) == "明天是 2026-05-30，星期六。"


def test_react_runner_reads_agno_camel_case_stream_events():
    class CamelCaseAgent:
        def run(self, *args, **kwargs):
            return iter(
                [
                    _Event("RunStarted"),
                    _Event("RunContent", "可以"),
                    _Event("RunContent", "正常回答。"),
                    _Event("RunCompleted", "可以正常回答。"),
                ]
            )

    result = AgentLoopRunner(agent_factory=lambda **kwargs: CamelCaseAgent()).run("你能做什么")

    assert result.status == "success"
    assert result.response_text == "可以正常回答。"
    assert "模型没有返回有效内容" not in result.response_text


def test_react_runner_passes_extra_context_to_agent_factory():
    calls = []

    class ContextAwareAgent:
        def run(self, *args, **kwargs):
            return iter([_Event("RunCompleted", "知道上一轮选项")])

    def factory(**kwargs):
        calls.append(kwargs)
        return ContextAwareAgent()

    result = AgentLoopRunner(agent_factory=factory).run(
        "A",
        extra_context_instructions="临时会话上下文：助手给过 A/B 选项",
    )

    assert result.response_text == "知道上一轮选项"
    assert calls[0]["extra_context_instructions"] == "临时会话上下文：助手给过 A/B 选项"
    assert calls[0]["query"] == "A"
    assert calls[0]["tool_call_limit"] == 8


def test_react_runner_passes_same_run_id_to_agno():
    run_calls = []

    class RecordingAgent:
        def run(self, *args, **kwargs):
            run_calls.append(kwargs)
            return iter([_Event("RunCompleted", "完成")])

    result = AgentLoopRunner(agent_factory=lambda **kwargs: RecordingAgent()).run(
        "执行一次模型任务",
        run_id="ask-fixed-001",
    )

    assert result.run_id == "ask-fixed-001"
    assert run_calls[0]["run_id"] == "ask-fixed-001"


def test_react_runner_emits_response_delta_progress():
    class StreamingAgent:
        def run(self, *args, **kwargs):
            return iter(
                [
                    _Event("RunContent", "第一段"),
                    _Event("RunContent", "第二段"),
                    _Event("RunCompleted", "第一段第二段"),
                ]
            )

    events = []
    result = AgentLoopRunner(agent_factory=lambda **kwargs: StreamingAgent()).run(
        "请流式回答",
        progress_callback=lambda name, payload: events.append((name, payload)),
    )

    assert result.response_text == "第一段第二段"
    assert [item[0] for item in events] == ["response_start", "response_delta", "response_delta"]
    assert [item[1]["delta"] for item in events if item[0] == "response_delta"] == ["第一段", "第二段"]


def test_react_runner_emits_deepseek_reasoning_progress_before_answer():
    class ReasoningAgent:
        def run(self, *args, **kwargs):
            return iter(
                [
                    _Event("ReasoningStarted"),
                    _Event("ReasoningContentDelta", reasoning_content="先分析用户目标。"),
                    _Event("ReasoningCompleted"),
                    _Event("RunContent", "最终回答"),
                    _Event("RunCompleted", "最终回答"),
                ]
            )

    events = []
    result = AgentLoopRunner(agent_factory=lambda **kwargs: ReasoningAgent()).run(
        "复杂问题",
        progress_callback=lambda name, payload: events.append((name, payload)),
    )

    assert result.response_text == "最终回答"
    assert [name for name, _ in events][:3] == [
        "reasoning_started",
        "reasoning_progress",
        "reasoning_completed",
    ]
    assert events[1][1]["content"] == "先分析用户目标。"


def test_react_runner_keeps_single_reasoning_segment_when_run_content_has_empty_content():
    class TokenReasoningAgent:
        output_schema = object()

        def run(self, *args, **kwargs):
            return iter(
                [
                    _Event("RunContent", "", reasoning_content="第一句思考，"),
                    _Event("RunContent", "", reasoning_content="第二句思考，"),
                    _Event("RunContent", "", reasoning_content="第三句思考。"),
                    _Event("RunContent", '{"blocks":[{"markdown":"最终结论","source_ids":[]}]}'),
                    _Event("RunCompleted", '{"blocks":[{"markdown":"最终结论","source_ids":[]}]}'),
                ]
            )

    events = []
    result = AgentLoopRunner(agent_factory=lambda **kwargs: TokenReasoningAgent()).run(
        "复杂问题",
        progress_callback=lambda name, payload: events.append((name, payload)),
    )

    started = [payload for name, payload in events if name == "reasoning_started"]
    completed = [payload for name, payload in events if name == "reasoning_completed"]
    assert len(started) == 1
    assert len(completed) == 1
    assert started[0]["segment"] == 1
    assert completed[0]["segment"] == 1
    assert completed[0]["content"] == "第一句思考，第二句思考，第三句思考。"
    assert result.response_text == "最终结论"


def test_react_runner_discards_pre_tool_chatter_and_parses_structured_blocks():
    class Tool:
        tool_call_id = "tool-weather"
        tool_name = "weather_lookup"
        tool_args = {"city": "甘孜"}
        result = "甘孜 3.5~17.8°C"
        tool_call_error = None

    class MultiStepStructuredAgent:
        output_schema = object()

        def run(self, *args, **kwargs):
            tool = Tool()
            final_json = (
                '{"blocks":[{"markdown":"### 未来一周关键点位天气","source_ids":[]},'
                '{"markdown":"| 区域 | 温度 |\\n|---|---|\\n| 甘孜 | 3.5~17.8°C |","source_ids":[]}]}'
            )
            return iter(
                [
                    _Event("RunContent", "刚才已有部分结果，我再补查一下。"),
                    _Event("ToolCallStarted", tool=tool),
                    _Event("ToolCallCompleted", tool=tool),
                    _Event("RunContent", final_json),
                    _Event("RunCompleted", f"刚才已有部分结果，我再补查一下。{final_json}"),
                ]
            )

    result = AgentLoopRunner(agent_factory=lambda **kwargs: MultiStepStructuredAgent()).run("去川西天气怎么样")

    assert result.status == "success"
    assert "blocks" not in result.response_text
    assert "我再补查一下" not in result.response_text
    assert "### 未来一周关键点位天气" in result.response_text
    assert "| 区域 | 温度 |\n|---|---|\n| 甘孜 | 3.5~17.8°C |" in result.response_text



def test_agent_loop_emits_tool_start_and_finish_progress():
    class Tool:
        tool_call_id = "tool-1"
        tool_name = "daily_report"
        tool_args = {"repos": ["/tmp/repo"]}
        result = "日报内容"
        tool_call_error = None

    class ToolAgent:
        def run(self, *args, **kwargs):
            tool = Tool()
            return iter(
                [
                    _Event("ToolCallStarted", tool=tool),
                    _Event("ToolCallCompleted", tool=tool),
                    _Event("RunCompleted", "完成"),
                ]
            )

    events = []
    result = AgentLoopRunner(agent_factory=lambda **kwargs: ToolAgent()).run(
        "生成日报",
        progress_callback=lambda name, payload: events.append((name, payload)),
    )

    assert result.status == "success"
    assert result.observations[0].tool == "daily_report"
    assert [item[0] for item in events] == [
        "iteration_started",
        "tool_start",
        "tool_end",
        "iteration_observed",
        "iteration_evaluated",
        "response_start",
    ]
    assert result.loop_events[-1]["decision"] == "respond"


def test_react_runner_does_not_record_duplicated_completed_tool_event_twice():
    class Tool:
        tool_name = "weather_query"
        tool_args = {"city": "火星", "forecast_days": 2}
        result = "没有找到城市"
        tool_call_error = True

        def __init__(self, tool_call_id):
            self.tool_call_id = tool_call_id

    class ToolAgent:
        def run(self, *args, **kwargs):
            first = Tool("tool-1")
            duplicate = Tool("tool-2")
            return iter(
                [
                    _Event("ToolCallCompleted", tool=first),
                    _Event("ToolCallCompleted", tool=duplicate),
                    _Event("RunCompleted", "查不到。"),
                ]
            )

    events = []
    result = AgentLoopRunner(agent_factory=lambda **kwargs: ToolAgent()).run(
        "查询火星天气；查不到就明确说查不到。",
        progress_callback=lambda name, payload: events.append((name, payload)),
    )

    assert result.status == "success"
    assert len(result.observations) == 1
    assert [name for name, _ in events].count("tool_end") == 1
    assert [name for name, _ in events].count("tool_blocked") == 1


def test_react_runner_stops_before_next_model_step_after_tool_cancellation():
    class Tool:
        tool_call_id = "tool-1"
        tool_name = "daily_report"
        tool_args = {}
        result = "已生成"
        tool_call_error = None

    class ToolAgent:
        def run(self, *args, **kwargs):
            tool = Tool()
            return iter(
                [
                    _Event("ToolCallStarted", tool=tool),
                    _Event("ToolCallCompleted", tool=tool),
                    _Event("RunContent", "不应该继续输出"),
                ]
            )

    checks = iter([False, False, False, True])
    result = AgentLoopRunner(agent_factory=lambda **kwargs: ToolAgent()).run(
        "生成日报",
        cancellation_check=lambda: next(checks, True),
    )

    assert result.status == "cancelled"
    assert result.response_text == "任务已取消。"
    assert len(result.observations) == 1


def test_react_runner_maps_agno_cancelled_event_to_cancelled_result():
    class CancelledAgent:
        def run(self, *args, **kwargs):
            return iter([_Event("RunCancelled", "Operation cancelled by user")])

    result = AgentLoopRunner(agent_factory=lambda **kwargs: CancelledAgent()).run("执行任务")

    assert result.status == "cancelled"
    assert result.error_stage == "cancellation"


def test_ask_service_forwards_response_stream_events(monkeypatch):
    class NoSkillGateway:
        def resolve(self, **kwargs):
            return None

    class StreamingRunner:
        def run(self, *args, progress_callback=None, **kwargs):
            progress_callback("response_start", {"observation_count": 0})
            progress_callback("response_delta", {"delta": "流式"})
            progress_callback("response_delta", {"delta": "回答"})
            return ExecutionResult(
                status="success",
                response_text="流式回答",
                plan=TaskPlan(intent="agent_loop", steps=[], missing_info=[], risk_level="low"),
                observations=[],
                stop_reason="任务完成",
                missing_info=[],
            )

    monkeypatch.setattr(
        "daily_agent.services.agent_service._safe_build_prompt_snapshot",
        lambda **kwargs: {"model_id": "test", "instructions": [], "tool_names": [], "skill_names": [], "prompt_text": kwargs["query"]},
    )
    events = []
    trace_events = []
    service = AskService(
        react_runner=StreamingRunner(),
        skill_gateway=NoSkillGateway(),
        memory_extractor=lambda text, user_id: [],
        store_factory=NoopStore,
    )

    result = service.run(
        "请回答",
        stream_callback=lambda name, payload: events.append((name, payload)),
        trace_callback=trace_events.append,
    )

    assert result.response_text == "流式回答"
    response_events = [item for item in events if item[0] != "status_updated"]
    assert response_events == [
        ("response_delta", {"delta": "流式"}),
        ("response_delta", {"delta": "回答"}),
        ("response_replace", {"text": "流式回答"}),
    ]
    assert any(name == "status_updated" for name, _ in events)
    assert trace_events[-1].key == "final"
    assert trace_events[-1].stage == "final_response"
    assert trace_events[-1].status == "success"


def test_ask_service_routes_plain_language_to_agent_loop(monkeypatch):
    class NoSkillGateway:
        def resolve(self, **kwargs):
            return None

    class RecordingRunner:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return ExecutionResult(
                status="success",
                response_text="loop ok",
                plan=TaskPlan(intent="agent_loop", steps=[], missing_info=[], risk_level="low"),
                observations=[],
                stop_reason="任务完成",
                missing_info=[],
                run_id=kwargs["run_id"],
                execution_source="agent_loop",
            )

    monkeypatch.setattr(
        "daily_agent.services.agent_service._safe_build_prompt_snapshot",
        lambda **kwargs: {"model_id": "test", "instructions": [], "tool_names": [], "skill_names": [], "prompt_text": kwargs["query"]},
    )
    runner = RecordingRunner()
    NoopStore.history_calls.clear()
    NoopStore.conversation_calls.clear()
    service = AskService(
        react_runner=runner,
        skill_gateway=NoSkillGateway(),
        memory_extractor=lambda text, user_id: [],
        store_factory=NoopStore,
    )

    result = service.run("帮我梳理一下思路", run_id="ask-api-001")

    assert result.execution.execution_source == "agent_loop"
    assert runner.calls[0]["query"] == "帮我梳理一下思路"
    assert runner.calls[0]["run_id"] == "ask-api-001"
    metadata = NoopStore.history_calls[-1]["metadata"]
    assert metadata["session_id"] == "default"
    assert metadata["response_text"] == "loop ok"
    assert metadata["plan"]["intent"] == "agent_loop"
    assert metadata["started_at"]
    assert metadata["finished_at"]
    assert metadata["duration_ms"] >= 0
    assert metadata["usage"] == {}
    assert NoopStore.conversation_calls[-1]["run_id"] == "ask-api-001"


def _ask_settings(
    *,
    agent_max_tool_calls: int = 16,
    agent_max_duration_seconds: int = 180,
) -> Settings:
    return Settings(
        model_provider="openai_compatible",
        model_id="deepseek-chat",
        model_api_key="sk-secret",
        model_base_url="https://model.example/v1",
        agno_telemetry=False,
        context_token_budget=4000,
        mysql_host="127.0.0.1",
        mysql_port=3306,
        mysql_user="root",
        mysql_password="password",
        mysql_database="sb_agent",
        agent_max_tool_calls=agent_max_tool_calls,
        agent_max_duration_seconds=agent_max_duration_seconds,
    )


def test_ask_service_builds_runner_from_configured_budget():
    settings = _ask_settings(agent_max_tool_calls=24, agent_max_duration_seconds=300)
    service = AskService(
        settings=settings,
        memory_extractor=lambda text, user_id: [],
        store_factory=NoopStore,
    )

    assert isinstance(service.react_runner, AgentLoopRunner)
    assert service.react_runner.max_tool_calls == 24
    assert service.react_runner.max_duration_seconds == 300


def test_ask_service_keeps_injected_react_runner_untouched():
    class CustomRunner:
        marker = "custom"

        def run(self, **kwargs):
            raise AssertionError("runner 不应被调用")

    settings = _ask_settings(agent_max_tool_calls=24)
    injected = CustomRunner()

    service = AskService(react_runner=injected, settings=settings)

    assert service.react_runner is injected


def test_ask_service_runs_daily_report_command_as_skill_command(monkeypatch):
    skill = SkillRecord(
        name="daily-report",
        description="生成日报",
        status="enabled",
        source="workspace",
        path="/tmp/daily-report",
        tools=("daily_report",),
        tool_name="daily_report",
        triggers=("/daily-report",),
        input_schema={"type": "object", "properties": {}},
        execution={"type": "python_callable", "entrypoint": "demo:run"},
        interaction_mode="react_tool",
    )

    class DailyReportGateway:
        def resolve(self, **kwargs):
            from daily_agent.skill_gateway import SkillRouteMatch

            return SkillRouteMatch(skill=skill, trigger="/daily-report", route_type="command", route_reason="命令触发")

    class Runtime:
        def run(self, **kwargs):
            return SkillRunResult(
                status="success",
                response_text="日报已生成",
                stop_reason="技能执行完成",
                inputs=kwargs["inputs"],
                output_text="日报已生成",
            )

    class FailingRunner:
        def run(self, **kwargs):
            raise AssertionError("/daily-report 不应该进入 Agent loop")

    monkeypatch.setattr(
        "daily_agent.services.agent_service._safe_build_prompt_snapshot",
        lambda **kwargs: {"model_id": "test", "instructions": [], "tool_names": [], "skill_names": [], "prompt_text": kwargs["query"]},
    )
    service = AskService(
        react_runner=FailingRunner(),
        skill_gateway=DailyReportGateway(),
        skill_runtime=Runtime(),
        memory_extractor=lambda text, user_id: [],
        store_factory=NoopStore,
    )

    result = service.run("/daily-report")

    assert result.execution.execution_source == "skill_command"
    assert result.response_text == "日报已生成"
    assert result.execution.observations[0].tool == "daily_report"


def test_react_runner_empty_model_content_has_plain_error():
    class EmptyAgent:
        def run(self, *args, **kwargs):
            return iter([])

    result = AgentLoopRunner(agent_factory=lambda **kwargs: EmptyAgent()).run("帮我看一下")

    assert result.status == "success"
    assert "模型没有返回有效内容" in result.response_text


def test_react_runner_reports_error_when_lookup_model_does_not_call_tools():
    class TextOnlyAgent:
        def run(self, *args, **kwargs):
            return iter([_Event("RunContent", "我来帮你查询美光科技的股票价格。"), _Event("RunCompleted", "我来帮你查询美光科技的股票价格。")])

    result = AgentLoopRunner(agent_factory=lambda **kwargs: TextOnlyAgent()).run("帮我查询美光科技股票票多少")

    assert result.status == "error"
    assert result.stop_reason == "模型未调用必要工具"
    assert "模型没有发起任何工具调用" in result.response_text


def test_react_runner_retries_transient_model_error_once():
    class BusyThenReadyAgent:
        def __init__(self):
            self.calls = 0

        def run(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return iter([_Event("RunError", "Service is too busy.")])
            return iter([_Event("RunCompleted", "重试后完成")])

    agent = BusyThenReadyAgent()
    result = AgentLoopRunner(agent_factory=lambda **kwargs: agent).run("解释一个概念")

    assert result.status == "success"
    assert result.response_text == "重试后完成"
    assert agent.calls == 2


def test_react_runner_keeps_weather_clarification_as_needs_input():
    class TextOnlyAgent:
        def run(self, *args, **kwargs):
            return iter([_Event("RunCompleted", "请问你想查询哪个城市明天的天气？")])

    result = AgentLoopRunner(agent_factory=lambda **kwargs: TextOnlyAgent()).run("帮我查明天的天气")

    assert result.status == "needs_input"
    assert result.response_text == "请问你想查询哪个城市明天的天气？"
    assert result.missing_info == ["城市或地区"]
    assert result.stop_reason == "缺少查询所需信息，等待用户补充"


def test_react_runner_saves_paused_run_for_confirmation(tmp_path):
    paused = _paused_run_output()

    class PausingAgent:
        def run(self, *args, **kwargs):
            return iter([_Event("RunPaused", "需要确认"), paused])

    pause_store = ReactPauseStore(tmp_path / "pause.json")
    result = AgentLoopRunner(agent_factory=lambda **kwargs: PausingAgent(), pause_store=pause_store).run(
        "帮我打开百度",
        user_id="u1",
        session_id="s1",
    )

    assert result.status == "needs_confirmation"
    assert result.response_text == "需要确认"
    assert pause_store.get(user_id="u1", session_id="s1").run_id == "run-1"


def test_react_runner_reports_tool_error_instead_of_waiting_for_confirmation(tmp_path):
    class Tool:
        tool_call_id = "tool-1"
        tool_name = "agent_browser"
        tool_args = {"action": "chat"}
        result = "AI_GATEWAY_API_KEY not set"
        tool_call_error = True

    class FailingThenPausedAgent:
        def run(self, *args, **kwargs):
            tool = Tool()
            return iter([
                _Event("ToolCallStarted", tool=tool),
                _Event("ToolCallError", tool=tool),
                _Event("RunPaused", "需要确认"),
            ])

    result = AgentLoopRunner(
        agent_factory=lambda **kwargs: FailingThenPausedAgent(),
        pause_store=ReactPauseStore(tmp_path / "pause.json"),
    ).run("打开百度", user_id="u1", session_id="s1")

    assert result.status == "error"
    assert result.response_text == "AI_GATEWAY_API_KEY not set"
    assert result.error_stage == "tool_execution"
    assert result.observations[0].error_message == "AI_GATEWAY_API_KEY not set"
    assert result.observations[0].output is None


def test_react_runner_does_not_report_empty_result_after_terminal_tool_timeout():
    class SuccessfulTool:
        tool_call_id = "tool-1"
        tool_name = "mcp_mysql_front_execute_sql"
        tool_args = {"query": "SELECT COUNT(*) FROM information_schema.TABLES"}
        result = "table_count 0"
        tool_call_error = None

    class TimedOutTool:
        tool_call_id = "tool-2"
        tool_name = "mcp_mysql_front_execute_sql"
        tool_args = {"query": "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA"}
        result = "MCP Tool 调用超时，服务在限定时间内没有返回结果。"
        tool_call_error = True

    class MixedResultAgent:
        def run(self, *args, **kwargs):
            first = SuccessfulTool()
            second = TimedOutTool()
            return iter([
                _Event("ToolCallStarted", tool=first),
                _Event("ToolCallCompleted", tool=first),
                _Event("ToolCallStarted", tool=second),
                _Event("ToolCallError", tool=second),
                _Event("RunCompleted", "数据库存在，但当前共有 0 张表。"),
            ])

    result = AgentLoopRunner(agent_factory=lambda **kwargs: MixedResultAgent()).run("数据库有多少表")

    assert result.status == "error"
    assert result.stop_reason == "最后一次工具调用失败"
    assert "当前无法确认查询结果" in result.response_text
    assert "0 张表" not in result.response_text
    assert result.observations[-1].status == "error"


def test_react_runner_does_not_mistake_run_paused_event_for_final_run_output(tmp_path):
    paused = _paused_run_output()

    class RealisticPausedEvent(_Event):
        status = RunStatus.paused
        tools = paused.tools

        def to_dict(self):
            return {"event": "RunPaused"}

    class PausingAgent:
        def run(self, *args, **kwargs):
            return iter([RealisticPausedEvent("RunPaused", "需要确认"), paused])

    result = AgentLoopRunner(
        agent_factory=lambda **kwargs: PausingAgent(),
        pause_store=ReactPauseStore(tmp_path / "pause.json"),
    ).run("执行浏览器操作", user_id="u1", session_id="s1")

    assert result.status == "needs_confirmation"
    assert result.response_text == "需要确认"


def test_react_runner_pause_message_overrides_partial_model_text(tmp_path):
    paused = _paused_run_output()

    class PausingAgent:
        def run(self, *args, **kwargs):
            return iter([_Event("RunContent", "我来帮你查询。"), _Event("RunPaused", "需要你确认后继续。"), paused])

    result = AgentLoopRunner(
        agent_factory=lambda **kwargs: PausingAgent(),
        pause_store=ReactPauseStore(tmp_path / "pause.json"),
    ).run("帮我查股票价格", user_id="u1", session_id="s1")

    assert result.status == "needs_confirmation"
    assert result.response_text == "需要你确认后继续。"


def test_react_runner_localizes_default_english_confirmation_message(tmp_path):
    paused = _paused_run_output()

    class PausingAgent:
        def run(self, *args, **kwargs):
            return iter([_Event("RunPaused", "I have tools to execute, but I need confirmation."), paused])

    result = AgentLoopRunner(
        agent_factory=lambda **kwargs: PausingAgent(),
        pause_store=ReactPauseStore(tmp_path / "pause.json"),
    ).run("执行浏览器操作", user_id="u1", session_id="s1")

    assert result.response_text == "这个操作需要你先确认。回复“确认”后我再继续，回复“取消”可以终止。"


def test_react_runner_continues_paused_run_after_confirmation(tmp_path):
    calls = []
    factory_calls = []

    class ContinuingAgent:
        def run(self, *args, **kwargs):
            raise AssertionError("确认后不应该重新开始 run")

        def continue_run(self, *args, **kwargs):
            calls.append(kwargs)
            return iter([_Event("RunContent", "已执行"), _Event("RunCompleted", "已执行")])

    pause_store = ReactPauseStore(tmp_path / "pause.json")
    pause_store.save(user_id="u1", session_id="s1", run_output=_paused_run_output())

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return ContinuingAgent()

    result = AgentLoopRunner(agent_factory=factory, pause_store=pause_store).run(
        "继续",
        user_id="u1",
        session_id="s1",
    )

    assert result.status == "success"
    assert result.response_text == "已执行"
    assert calls[0]["run_response"].requirements[0].confirmation is True
    assert calls[0]["run_response"].tools[0].confirmed is True
    assert factory_calls[0]["confirmed_tool_names"] == {"core"}
    assert pause_store.get(user_id="u1", session_id="s1") is None


def test_react_runner_uses_request_run_id_when_continuing_paused_run(tmp_path):
    calls = []

    class ContinuingAgent:
        def continue_run(self, *args, **kwargs):
            calls.append(kwargs)
            return iter([_Event("RunCompleted", "已执行")])

    pause_store = ReactPauseStore(tmp_path / "pause.json")
    pause_store.save(user_id="u1", session_id="s1", run_output=_paused_run_output())

    result = AgentLoopRunner(agent_factory=lambda **kwargs: ContinuingAgent(), pause_store=pause_store).run(
        "继续",
        user_id="u1",
        session_id="s1",
        run_id="ask-current-001",
    )

    assert result.run_id == "ask-current-001"
    assert calls[0]["run_id"] == "ask-current-001"


def test_agent_loop_returns_structured_error_when_model_unavailable():
    def failing_factory(**kwargs):
        raise MissingConfigError("缺模型配置")

    result = AgentLoopRunner(agent_factory=failing_factory).run("帮我处理", run_id="ask-1")

    assert result.status == "error"
    assert result.response_text == "缺模型配置"
    assert result.execution_source == "error"
    assert result.error_stage == "model_config"
    assert result.run_id == "ask-1"


def test_react_runner_retries_structured_format_failure_without_tools():
    calls = []

    class StructuredAgent:
        output_schema = object()

        def run(self, *args, **kwargs):
            calls.append(args[0] if args else kwargs)
            if len(calls) == 1:
                return iter([
                    _Event("RunContent", None, reasoning_content="只有思考"),
                    _Event("RunCompleted", None),
                ])
            return iter([
                _Event("RunContent", '{"blocks":[{"markdown":"回答正文","source_ids":[]}]}'),
                _Event("RunCompleted", '{"blocks":[{"markdown":"回答正文","source_ids":[]}]}'),
            ])

    result = AgentLoopRunner(agent_factory=lambda **kwargs: StructuredAgent()).run(
        "回答一个问题", run_id="ask-1"
    )

    assert result.status == "success"
    assert result.response_text == "回答正文"
    assert len(calls) == 2


def _failing_agent_factory(**kwargs):
    raise AssertionError("简单寒暄不应该创建模型 Agent")


class NoopStore:
    history_calls = []
    conversation_calls = []

    def record_conversation_turn(self, **kwargs):
        self.conversation_calls.append(kwargs)
        return None

    def refresh_conversation_summary(self, **kwargs):
        return None

    def add_task_history(self, **kwargs):
        self.history_calls.append(kwargs)
        return None


def _paused_run_output():
    tool = ToolExecution(
        tool_call_id="tool-1",
        tool_name="core",
        tool_args={"task": "打开百度"},
        requires_confirmation=True,
    )
    requirement = RunRequirement(tool)
    return RunOutput(
        run_id="run-1",
        session_id="s1",
        tools=[tool],
        requirements=[requirement],
        messages=[],
        status=RunStatus.paused,
    )
