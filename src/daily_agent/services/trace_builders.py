"""构建 Tool 观察和最终结果的安全 Trace。"""

from daily_agent.executor import ExecutionResult, StepObservation
from daily_agent.services.agent_models import TraceEvent
from daily_agent.services.rendering import summarize_text
from daily_agent.skill_catalog import get_skill_name_for_tool
from daily_agent.skill_runtime import (
    SKILL_STATUS_ERROR,
    SKILL_STATUS_NEEDS_CONFIRMATION,
    SKILL_STATUS_NEEDS_INPUT,
    SKILL_STATUS_SUCCESS,
)


def observation_to_trace_event(observation: StepObservation) -> TraceEvent:
    skill_name = get_skill_name_for_tool(observation.tool or "") if observation.tool else None
    details = {
        "tool": observation.tool,
        "skill_name": skill_name,
        "inputs": dict(observation.inputs),
        "duration_ms": observation.duration_ms,
        "attempts": observation.attempts,
        "output_summary": summarize_text(observation.output or "", 240) if observation.output else None,
        "error_message": observation.error_message,
    }
    summary = (
        f"{observation.name} 执行失败，共尝试 {observation.attempts} 次。"
        if observation.status == SKILL_STATUS_ERROR
        else f"{observation.name} 执行完成，耗时 {observation.duration_ms}ms。"
    )
    return TraceEvent(
        key=f"tool-{observation.index}-finished",
        stage="tool_finished",
        status=observation.status,
        title=observation.name,
        summary=summary,
        details=details,
    )


def final_trace_summary(execution: ExecutionResult) -> str:
    if execution.status == SKILL_STATUS_SUCCESS:
        return "本轮执行完成，已生成给用户的最终回复。"
    if execution.status == SKILL_STATUS_NEEDS_INPUT:
        return "因为缺少必要信息，本轮先返回追问提示。"
    if execution.status == SKILL_STATUS_NEEDS_CONFIRMATION:
        return "因为需要用户确认，本轮先暂停等待。"
    if execution.status == SKILL_STATUS_ERROR:
        return f"执行中断：{execution.stop_reason}"
    return execution.stop_reason
