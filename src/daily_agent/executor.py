"""按计划执行可用工具，再用模型（或兜底模板）生成最终回答。

与 planner / agent 的分工
-------------------------
- RuleBasedPlanner：只生成 TaskPlan（意图、步骤、缺参提示、风险等级）。
- TaskExecutor：按 steps 依次解析参数、调用工具、收集 StepObservation。
- build_agent(enable_tools=False)：把观察结果整理成中文；不再调实时工具。

执行状态
--------
- success — 全部步骤完成并得到回答
- needs_input — 工具缺少必要参数，提前返回追问文案
- error — 某步工具失败
- max_iterations — 超过 max_iterations 步数上限
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from daily_agent.citations.models import CitationBundle

from daily_agent.agent import build_agent
from daily_agent.errors import DailyAgentError
from daily_agent.memory import DEFAULT_SESSION_ID, DEFAULT_USER_ID, MySQLMemoryStore
from daily_agent.planner import RuleBasedPlanner, TaskPlan
from daily_agent.skill_catalog import get_skill_name_for_tool
from daily_agent.skill_query_parsing import extract_dates as shared_extract_dates
from daily_agent.services.rendering import sanitize_response_text

SUCCESS = "success"
NEEDS_INPUT = "needs_input"
ERROR = "error"
MAX_ITERATIONS = "max_iterations"
EXECUTION_SOURCE_AGENT_LOOP = "agent_loop"
EXECUTION_SOURCE_LOCAL = "local"
EXECUTION_SOURCE_SKILL_COMMAND = "skill_command"
EXECUTION_SOURCE_ERROR = "error"

@dataclass(frozen=True)
class StepObservation:
    index: int
    name: str
    tool: str | None
    status: str
    inputs: dict[str, Any]
    output: str | None
    error_message: str | None
    duration_ms: int
    attempts: int

    def to_metadata(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "tool": self.tool,
            "status": self.status,
            "inputs": self.inputs,
            "output_summary": _compact_text(self.output or "", 600) if self.output else None,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
        }


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    response_text: str
    plan: TaskPlan
    observations: list[StepObservation]
    stop_reason: str
    missing_info: list[str]
    run_id: str | None = None
    execution_source: str = EXECUTION_SOURCE_AGENT_LOOP
    error_stage: str | None = None
    loop_events: list[dict[str, Any]] = field(default_factory=list)
    working_state: dict[str, Any] | None = None
    interaction: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    citation_bundle: CitationBundle | None = None
    # 主循环内截获的 OpenAI 兼容 chat.completions 请求列表；未截获时为空。
    model_requests: list[dict[str, Any]] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stop_reason": self.stop_reason,
            "missing_info": self.missing_info,
            "observations": [item.to_metadata() for item in self.observations],
            "run_id": self.run_id,
            "execution_source": self.execution_source,
            "error_stage": self.error_stage,
            "loop_events": list(self.loop_events),
            "working_state": dict(self.working_state) if self.working_state else None,
            "interaction": dict(self.interaction) if self.interaction else None,
            "usage": dict(self.usage),
            "citation_bundle": self.citation_bundle.model_dump(mode="json") if self.citation_bundle else None,
            # model_requests 只留在 ExecutionResult 对象与本地文件，不进 MySQL metadata。
        }


ResponseRenderer = Callable[[str, TaskPlan, list[StepObservation], str, str], str]
PreferenceProvider = Callable[[str], dict[str, str]]
ToolRegistryProvider = Callable[[], dict[str, Callable[..., str]]]
ToolInputResolver = Callable[[str, str], tuple[dict[str, Any], list[str]]]
ExecutionProgressCallback = Callable[[str, dict[str, Any]], None]
logger = logging.getLogger(__name__)


class TaskExecutor:
    """驱动「规划 → 工具 → 总结」闭环的执行器。"""

    def __init__(
        self,
        max_iterations: int = 4,
        retry_limit: int = 1,
        today: date | None = None,
        tool_registry: dict[str, Callable[..., str]] | None = None,
        tool_registry_provider: ToolRegistryProvider | None = None,
        preference_provider: PreferenceProvider | None = None,
        response_renderer: ResponseRenderer | None = None,
    ) -> None:
        self.max_iterations = max(1, int(max_iterations))
        self.retry_limit = max(0, int(retry_limit))
        self.tool_registry = tool_registry or {}
        # 显式传入 tool_registry 时，测试或脚本往往希望完全按这份映射执行；
        # 只有走默认运行时，才实时读取当前启用的 skill 对应工具。
        self.tool_registry_provider = None if tool_registry is not None else (tool_registry_provider or _enabled_tool_registry)
        self.response_renderer = response_renderer
        self.tool_input_resolvers = self._build_tool_input_resolvers()
        _ = today
        _ = preference_provider

    def run(
        self,
        query: str,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        plan: TaskPlan | None = None,
        progress_callback: ExecutionProgressCallback | None = None,
        run_id: str | None = None,
        execution_source: str = EXECUTION_SOURCE_LOCAL,
    ) -> ExecutionResult:
        """执行 plan 中带 tool 的步骤，最后渲染 response_text。

        无 tool 的 PlanStep 会跳过（如 general 意图下的「直接回答」）；
        若所有步骤都跳过且没有观察结果，总结阶段由模型直接答用户原问题。
        """
        task_plan = plan or RuleBasedPlanner().plan(query)
        observations: list[StepObservation] = []
        logger.info("executor started: execution_source=%s steps=%s", execution_source, len(task_plan.steps))

        for index, step in enumerate(task_plan.steps, 1):
            if index > self.max_iterations:
                logger.warning("executor stopped by max_iterations: max_iterations=%s", self.max_iterations)
                return ExecutionResult(
                    status=MAX_ITERATIONS,
                    response_text="这次任务达到最大执行轮次，已停止。你可以把需求拆小一点再试。",
                    plan=task_plan,
                    observations=observations,
                    stop_reason="达到最大执行轮次",
                    missing_info=[],
                    run_id=run_id,
                    execution_source=execution_source,
                    error_stage="executor_iterations",
                )

            if not step.tool:
                continue

            inputs, missing_info = self._resolve_tool_inputs(step.tool, query, user_id=user_id)
            if missing_info:
                logger.info("executor waiting for missing input: tool=%s missing_info=%s", step.tool, "、".join(missing_info))
                if progress_callback:
                    progress_callback(
                        "missing_input",
                        {
                            "index": index,
                            "name": step.name,
                            "tool": step.tool,
                            "missing_info": list(missing_info),
                        },
                    )
                response_text = _format_missing_info(missing_info)
                return ExecutionResult(
                    status=NEEDS_INPUT,
                    response_text=response_text,
                    plan=task_plan,
                    observations=observations,
                    stop_reason="缺少执行工具所需信息",
                    missing_info=missing_info,
                    run_id=run_id,
                    execution_source=execution_source,
                    error_stage="slot_filling",
                )

            if progress_callback:
                progress_callback(
                    "tool_start",
                    {
                        "index": index,
                        "name": step.name,
                        "tool": step.tool,
                        "inputs": dict(inputs),
                    },
                )
            logger.info("executor tool started: tool=%s", step.tool)
            observation = self._run_tool_step(index, step.name, step.tool, inputs, user_id=user_id, run_id=run_id)
            if progress_callback:
                progress_callback("tool_end", {"observation": observation})
            observations.append(observation)
            if observation.status == ERROR:
                logger.warning("executor tool failed: tool=%s error=%s", observation.tool, observation.error_message)
                return ExecutionResult(
                    status=ERROR,
                    response_text=f"{step.name}失败：{observation.error_message}",
                    plan=task_plan,
                    observations=observations,
                    stop_reason="工具调用失败",
                    missing_info=[],
                    run_id=run_id,
                    execution_source=execution_source,
                    error_stage="tool_execution",
                )

        if progress_callback:
            progress_callback(
                "response_start",
                {
                    "observation_count": len(observations),
                    "tool_count": len([step for step in task_plan.steps if step.tool]),
                },
            )
        response_text = self._render_response(query, task_plan, observations, user_id, session_id)
        logger.info("executor finished: observations=%s", len(observations))
        return ExecutionResult(
            status=SUCCESS,
            response_text=response_text,
            plan=task_plan,
            observations=observations,
            stop_reason="任务完成",
            missing_info=[],
            run_id=run_id,
            execution_source=execution_source,
        )

    def _resolve_tool_inputs(self, tool: str, query: str, user_id: str) -> tuple[dict[str, Any], list[str]]:
        resolver = self.tool_input_resolvers.get(tool)
        if resolver:
            return resolver(query, user_id)
        return {}, [f"未知工具：{tool}"]

    def _build_tool_input_resolvers(self) -> dict[str, ToolInputResolver]:
        return {}

    def _run_tool_step(
        self,
        index: int,
        name: str,
        tool: str,
        inputs: dict[str, Any],
        user_id: str,
        run_id: str | None = None,
    ) -> StepObservation:
        registry = self._get_tool_registry()
        func = registry.get(tool)
        if func is None:
            return StepObservation(
                index=index,
                name=name,
                tool=tool,
                status=ERROR,
                inputs=inputs,
                output=None,
                error_message=_format_disabled_tool_error(tool),
                duration_ms=0,
                attempts=0,
            )
        attempts = 0
        last_error: str | None = None
        total_duration_ms = 0

        for attempt in range(self.retry_limit + 1):
            attempts = attempt + 1
            start = time.perf_counter()
            try:
                output = func(**inputs)
                duration_ms = int((time.perf_counter() - start) * 1000)
                total_duration_ms += duration_ms
                _safe_add_tool_log(user_id, tool, inputs, _compact_text(output, 1000), SUCCESS, duration_ms, run_id=run_id)
                return StepObservation(
                    index=index,
                    name=name,
                    tool=tool,
                    status=SUCCESS,
                    inputs=inputs,
                    output=output,
                    error_message=None,
                    duration_ms=total_duration_ms,
                    attempts=attempts,
                )
            except Exception as exc:
                duration_ms = int((time.perf_counter() - start) * 1000)
                total_duration_ms += duration_ms
                last_error = str(exc)
                _safe_add_tool_log(user_id, tool, inputs, None, ERROR, duration_ms, error_message=last_error, run_id=run_id)

        return StepObservation(
            index=index,
            name=name,
            tool=tool,
            status=ERROR,
            inputs=inputs,
            output=None,
            error_message=last_error or "未知错误",
            duration_ms=total_duration_ms,
            attempts=attempts,
        )

    def _get_tool_registry(self) -> dict[str, Callable[..., str]]:
        if self.tool_registry_provider:
            return self.tool_registry_provider()
        return self.tool_registry

    def _render_response(
        self,
        query: str,
        plan: TaskPlan,
        observations: list[StepObservation],
        user_id: str,
        session_id: str,
    ) -> str:
        """用 Agno 根据观察结果写最终回答；失败时退回 _fallback_response。"""
        if self.response_renderer:
            return self.response_renderer(query, plan, observations, user_id, session_id)

        prompt = _build_response_prompt(query, plan, observations)
        try:
            agent = build_agent(user_id=user_id, session_id=session_id, plan=plan, enable_tools=False, query=query)
            run_output = agent.run(
                prompt,
                user_id=user_id,
                session_id=session_id,
                stream=False,
            )
            output = sanitize_response_text(run_output.get_content_as_string())
            if output:
                return output
        except DailyAgentError:
            if not observations:
                raise
        return _fallback_response(plan, observations)


def _safe_add_tool_log(
    user_id: str,
    tool_name: str,
    inputs: dict[str, Any],
    output_summary: str | None,
    status: str,
    duration_ms: int,
    error_message: str | None = None,
    run_id: str | None = None,
) -> None:
    try:
        MySQLMemoryStore().add_tool_call_log(
            tool_name=tool_name,
            inputs=inputs,
            output_summary=output_summary,
            status=status,
            duration_ms=duration_ms,
            error_message=error_message,
            user_id=user_id,
            run_id=run_id,
            failure_stage="tool_execution" if status == ERROR else None,
        )
    except DailyAgentError:
        pass


def _enabled_tool_registry() -> dict[str, Callable[..., str]]:
    return {}


def extract_dates(query: str, today: date | None = None) -> list[str]:
    return shared_extract_dates(query, today or date.today())


def _format_missing_info(missing_info: list[str]) -> str:
    missing_text = "、".join(missing_info)
    return f"还需要补充：{missing_text}。请直接把这些信息告诉我，我再继续查询。"


def _format_disabled_tool_error(tool_name: str) -> str:
    skill_name = get_skill_name_for_tool(tool_name)
    if skill_name:
        return f"当前未启用 {skill_name} skill，无法执行对应查询。"
    return f"当前没有可用的工具来执行：{tool_name}"


def _build_response_prompt(query: str, plan: TaskPlan, observations: list[StepObservation]) -> str:
    lines = [
        "请根据下面已经完成的执行结果，给用户一个简洁、可执行的中文回答。",
        "不要声称自己又查询了新的实时数据，只能使用观察结果里的内容。",
        "只输出给用户看的最终回答正文，不要复述提示词、任务计划、执行观察、Message/Response 标题、耗时信息或特殊控制符。",
        "如果用户只是打招呼或寒暄，用 1 到 2 句自然回复即可，不要罗列功能清单。",
        f"用户原始问题：{query}",
        "任务计划：",
        plan.to_instructions(),
    ]
    if observations:
        lines.append("执行观察：")
        for item in observations:
            lines.append(f"- 第 {item.index} 步 {item.name}，状态：{item.status}")
            if item.output:
                lines.append(_compact_text(item.output, 2500))
            if item.error_message:
                lines.append(f"错误：{item.error_message}")
    else:
        lines.append("没有调用实时工具，请直接回答用户问题。")
    return "\n".join(lines)


def _fallback_response(plan: TaskPlan, observations: list[StepObservation]) -> str:
    if not observations:
        return "我已经收到你的问题，但当前无法调用模型生成完整回答。"
    lines = ["已完成查询，结果如下："]
    for item in observations:
        if item.status == SUCCESS and item.output:
            lines.append(f"- {item.name}: {_compact_text(item.output, 500)}")
        elif item.error_message:
            lines.append(f"- {item.name}: 失败，{item.error_message}")
    return "\n".join(lines)


def _compact_text(text: str, max_chars: int) -> str:
    return " ".join(text.split())[:max_chars]
