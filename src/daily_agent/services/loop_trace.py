"""把 Agent 循环进度转换成前端可展示的安全轨迹。"""

from __future__ import annotations

from typing import Any

from daily_agent.executor import StepObservation
from daily_agent.services.agent_models import TraceEvent
from daily_agent.services.rendering import sanitize_reasoning_text, summarize_text
from daily_agent.skill_catalog import get_skill_name_for_tool
from daily_agent.tool_progress import business_arguments, progress_message


def chronological_execution_trace(
    loop_events: list[dict[str, Any]], observations: list[StepObservation],
) -> list[TraceEvent]:
    """按原始执行顺序重建历史，避免把所有说明集中到所有工具之前。"""
    result: list[TraceEvent] = []
    pending = {item.index: item for item in observations}
    for event in loop_events:
        trace = loop_event_to_trace_event(event)
        if trace is not None:
            result.append(trace)
        if event.get("event") == "iteration_observed":
            observation = pending.pop(int(event.get("iteration") or 0), None)
            if observation is not None:
                result.append(_observation_trace(observation))
    # 兼容没有循环事件的旧执行器，以及工具结束前异常中断的记录。
    result.extend(_observation_trace(item) for item in pending.values())
    return result


def executor_progress_to_trace_event(event_name: str, payload: dict[str, Any]) -> TraceEvent | None:
    if event_name in {"reasoning_started", "reasoning_completed"}:
        return loop_event_to_trace_event({"event": event_name, **payload})
    if event_name == "reasoning_progress":
        return _reasoning_trace(payload, status="running")
    if event_name.startswith("iteration_"):
        return loop_event_to_trace_event({"event": event_name, **payload})
    if event_name == "tool_start":
        index = int(payload.get("index") or 0)
        return TraceEvent(
            key=f"tool-{index}-started",
            stage="tool_started",
            status="running",
            title=str(payload.get("name") or "执行工具"),
            summary="正在调用工具并等待返回结果。",
            details={"tool": payload.get("tool"), "inputs": business_arguments(dict(payload.get("inputs") or {})), "iteration": index},
        )
    if event_name == "tool_end":
        observation = payload.get("observation")
        return _observation_trace(observation) if isinstance(observation, StepObservation) else None
    if event_name == "tool_blocked":
        index = int(payload.get("index") or 0)
        return TraceEvent(
            key=f"tool-{index}-finished",
            stage="tool_blocked",
            status="blocked",
            title=str(payload.get("name") or "工具调用已拦截"),
            summary=str(payload.get("reason") or "循环守卫阻止了这次工具调用。"),
            details={
                "tool": payload.get("tool"),
                "inputs": dict(payload.get("inputs") or {}),
                "iteration": index,
                "blocked": True,
            },
        )
    if event_name == "response_start":
        count = int(payload.get("observation_count") or 0)
        return TraceEvent(
            key="final",
            stage="final_response",
            status="running",
            title="整理回复",
            summary="工具结果已返回，正在组织最终答复。" if count else "正在直接整理回答。",
            details={"observation_count": count},
        )
    if event_name == "citation_verification_started":
        return TraceEvent(
            key="citations",
            stage="citation_verification",
            status="running",
            title="核对来源",
            summary="段落与来源已经同步生成，正在读取被引用网页并核对原文。",
            details={},
        )
    if event_name == "missing_input":
        missing_info = [str(item) for item in (payload.get("missing_info") or [])]
        return TraceEvent(
            key="input",
            stage="waiting_input",
            status="needs_input",
            title="等待补充信息",
            summary=f"还缺少 {'、'.join(missing_info)}，本轮先暂停。",
            details={"missing_info": missing_info, "tool": payload.get("tool"), "step_name": payload.get("name")},
        )
    return None


def loop_event_to_trace_event(event: dict[str, Any]) -> TraceEvent | None:
    event_name = str(event.get("event") or "")
    if event_name == "reasoning_started":
        segment = int(event.get("segment") or 1)
        return TraceEvent(
            key=_reasoning_key(segment),
            stage="reasoning",
            status="running",
            title=_reasoning_title(segment),
            summary="模型正在分析问题、判断下一步操作。",
            details={
                "model_mode": event.get("model_mode") or "thinking",
                "segment": segment,
            },
        )
    if event_name == "reasoning_completed":
        return _reasoning_trace(event, status="success")
    iteration = int(event.get("iteration") or 0)
    if not iteration:
        return None
    details = {key: value for key, value in event.items() if key != "event"}
    if event_name == "iteration_started":
        inputs = dict(event.get("inputs") or {})
        progress = progress_message(inputs)
        details["inputs"] = business_arguments(inputs)
        details["public_progress"] = bool(progress)
        return TraceEvent(
            key=f"iteration-{iteration}-started",
            stage="iteration_started",
            status="success",
            title=f"第 {iteration} 轮：选择动作",
            summary=progress or f"正在准备{event.get('name') or '执行下一步'}。",
            details=details,
        )
    if event_name == "iteration_observed":
        blocked = event.get("status") == "blocked"
        return TraceEvent(
            key=f"iteration-{iteration}-observed",
            stage="iteration_observed",
            status="blocked" if blocked else str(event.get("status") or "success"),
            title=f"第 {iteration} 轮：读取结果",
            summary="调用被循环守卫拦截。" if blocked else "工具结果已返回，正在判断下一步。",
            details=details,
        )
    if event_name == "iteration_evaluated":
        decision = str(event.get("decision") or "respond")
        title = {"continue": "继续下一轮", "respond": "进入最终回复", "stop": "停止循环"}.get(decision, "完成评估")
        return TraceEvent(
            key=f"iteration-{iteration}-evaluated",
            stage="iteration_evaluated",
            status="success",
            title=f"第 {iteration} 轮：{title}",
            summary=str(event.get("reason") or "已根据本轮结果决定下一步。"),
            details=details,
        )
    return None


def _reasoning_key(segment: int) -> str:
    return "reasoning" if segment <= 1 else f"reasoning-{segment}"


def _reasoning_title(segment: int) -> str:
    return "深度思考" if segment <= 1 else f"第 {segment} 次思考"


def _reasoning_trace(payload: dict[str, Any], *, status: str) -> TraceEvent:
    segment = int(payload.get("segment") or 1)
    content, truncated = sanitize_reasoning_text(str(payload.get("content") or ""))
    return TraceEvent(
        key=_reasoning_key(segment),
        stage="reasoning",
        status=status,
        title=_reasoning_title(segment),
        summary="模型已完成分析。" if status == "success" else "模型正在持续分析。",
        details={
            "reasoning_content": content or None,
            "character_count": int(payload.get("character_count") or 0),
            "segment": segment,
            "truncated": truncated,
        },
    )


def _observation_trace(observation: StepObservation) -> TraceEvent:
    skill_name = get_skill_name_for_tool(observation.tool or "") if observation.tool else None
    summary = (
        f"{observation.name} 执行失败，共尝试 {observation.attempts} 次。"
        if observation.status == "error"
        else f"{observation.name} 执行完成，耗时 {observation.duration_ms}ms。"
    )
    return TraceEvent(
        key=f"tool-{observation.index}-finished",
        stage="tool_finished",
        status=observation.status,
        title=observation.name,
        summary=summary,
        details={
            "tool": observation.tool,
            "skill_name": skill_name,
            "inputs": business_arguments(dict(observation.inputs)),
            "duration_ms": observation.duration_ms,
            "attempts": observation.attempts,
            "output_summary": summarize_text(observation.output or "", 240) if observation.output else None,
            "error_message": observation.error_message,
            "iteration": observation.index,
        },
    )
