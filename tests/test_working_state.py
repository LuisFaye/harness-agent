from types import SimpleNamespace

from daily_agent.services.agent_models import TraceEvent
from daily_agent.working_state import WorkingStateTracker, render_working_state_instruction


def test_working_state_tracks_tool_and_terminal_status():
    tracker = WorkingStateTracker(run_id="ask-1", query="查询成都天气", tool_call_limit=8)

    tracker.apply_trace(
        TraceEvent(
            key="tool-1",
            stage="tool_started",
            status="running",
            title="查询天气",
            summary="正在调用",
            details={"iteration": 1, "tool": "weather_query"},
        )
    )
    observed = tracker.apply_trace(
        TraceEvent(
            key="tool-1-finished",
            stage="tool_finished",
            status="success",
            title="查询天气",
            summary="执行完成",
            details={"tool": "weather_query", "output_summary": "成都 28°C"},
        )
    )
    final = tracker.finish(status="success", stop_reason="任务完成", elapsed_seconds=3)

    assert observed["phase"] == "evaluating"
    assert observed["budget"]["tool_calls"] == 1
    assert observed["evidence_summaries"][0]["summary"] == "成都 28°C"
    assert final["phase"] == "completed"
    assert final["current_action"] == "任务已完成"


def test_working_state_instruction_is_compact_and_excludes_raw_details():
    tracker = WorkingStateTracker(run_id="ask-2", query="检查项目")
    tracker.apply_trace(
        SimpleNamespace(
            stage="tool_finished",
            status="success",
            title="检查结构",
            summary="完成",
            details={"tool": "project_inspector", "output_summary": "发现两个入口"},
        )
    )

    instruction = render_working_state_instruction(tracker.snapshot())

    assert "当前任务工作状态" in instruction
    assert "发现两个入口" in instruction
    assert "不得据此编造细节" in instruction
