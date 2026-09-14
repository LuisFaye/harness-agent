from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable

from daily_agent.client_context import build_client_context_instructions
from daily_agent.config import Settings, load_settings
from daily_agent.context import MySQLContextBuilder
from daily_agent.request_preparation import preparation_scope
from daily_agent.conversation_title import generate_conversation_title, schedule_conversation_title
from daily_agent.errors import DailyAgentError
from daily_agent.executor import (
    EXECUTION_SOURCE_AGENT_LOOP,
    EXECUTION_SOURCE_ERROR,
    EXECUTION_SOURCE_LOCAL,
    EXECUTION_SOURCE_SKILL_COMMAND,
    ExecutionResult,
    StepObservation,
)
from daily_agent.logging_setup import bind_log_context, clear_log_context
from daily_agent.memory import DEFAULT_SESSION_ID, DEFAULT_USER_ID, MySQLMemoryStore
from daily_agent.memory_extractor import ExtractedMemory, persist_extracted_memories
from daily_agent.planner import PlanStep, TaskPlan
from daily_agent.react_runner import AgentLoopRunner
from daily_agent.react_text import local_response_for_simple_input
from daily_agent.response_constraints import (
    enforce_response_char_limit,
    extract_response_char_limit,
    extract_response_format,
    repair_format_response,
)
from daily_agent.response_route import should_use_fast_response
from daily_agent.runtime_state import generate_run_id
from daily_agent.agent import PromptSnapshot, build_prompt_snapshot
from daily_agent.artifact_deliverables import register_run_deliverables_quietly
from daily_agent.artifact_service import ArtifactService
from daily_agent.skill_catalog import get_skill_name_for_tool
from daily_agent.skill_gateway import SkillGateway, SkillRouteMatch
from daily_agent.skill_runtime import (
    SKILL_STATUS_ERROR,
    SKILL_STATUS_NEEDS_CONFIRMATION,
    SKILL_STATUS_NEEDS_INPUT,
    SKILL_STATUS_SUCCESS,
    SkillRuntime,
)
from daily_agent.skill_session import SkillSessionStore
from daily_agent.skill_slot_filling import SkillSlotFiller, build_missing_info_prompt
from daily_agent.services.rendering import summarize_text
from daily_agent.services.agent_cancellation import (
    build_cancelled_execution as _build_cancelled_execution,
    build_cancellation_trace_event as _build_cancellation_trace_event,
)
from daily_agent.services.agent_models import AskServiceResult, TraceEvent
from daily_agent.services.loop_trace import chronological_execution_trace, executor_progress_to_trace_event, loop_event_to_trace_event
from daily_agent.services.trace_builders import (
    final_trace_summary as _build_final_trace_summary,
    observation_to_trace_event as _observation_to_trace_event,
)
from daily_agent.services.conversation_recording import record_conversation_safely
from daily_agent.working_state import WorkingStateTracker
from daily_agent.citations.composer import reanchor_bundle
from daily_agent.user_workspace import user_workspace_root

StoreFactory = Callable[[], MySQLMemoryStore]
MemoryExtractor = Callable[[str, str], list[ExtractedMemory]]
TraceCallback = Callable[["TraceEvent"], None]
StreamCallback = Callable[[str, dict[str, Any]], None]
CancellationCheck = Callable[[], bool]
logger = logging.getLogger(__name__)

class AskService:
    """封装 ask 主流程，供 CLI 和 API 复用。

    主链路只有两种入口：
    - 未完成 skill 会话或显式 `/command`，走确定性 skill command；
    - 普通自然语言，进入模型工具调用 loop。
    """

    def __init__(
        self,
        react_runner: AgentLoopRunner | None = None,
        settings: Settings | None = None,
        memory_extractor: MemoryExtractor | None = None,
        store_factory: StoreFactory | None = None,
        skill_gateway: SkillGateway | None = None,
        skill_runtime: SkillRuntime | None = None,
        skill_session_store: SkillSessionStore | None = None,
        skill_slot_filler: SkillSlotFiller | None = None,
        working_directory: str | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        if react_runner is not None:
            self.react_runner = react_runner
        else:
            self.react_runner = AgentLoopRunner(
                max_tool_calls=self.settings.agent_max_tool_calls,
                max_duration_seconds=self.settings.agent_max_duration_seconds,
                max_inline_tokens=self.settings.agent_max_inline_tokens,
            )
        self.memory_extractor = memory_extractor or persist_extracted_memories
        self.store_factory = store_factory or MySQLMemoryStore
        self.working_directory = working_directory or "."
        self.skill_gateway = skill_gateway or SkillGateway()
        self.skill_runtime = skill_runtime or SkillRuntime(
            working_directory=self.working_directory,
            store_factory=self.store_factory,
        )
        self.skill_session_store = skill_session_store or SkillSessionStore()
        self.skill_slot_filler = skill_slot_filler or SkillSlotFiller()

    @preparation_scope
    def run(
        self,
        query: str,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        trace_callback: TraceCallback | None = None,
        stream_callback: StreamCallback | None = None,
        extra_context_instructions: str | None = None,
        attachment_images: list[Any] | None = None,
        attachment_ids: list[str] | None = None,
        run_id: str | None = None,
        cancellation_check: CancellationCheck | None = None,
        suppress_user_message: bool = False,
        user_message_persisted: bool = False,
        client_messages: list[Any] | None = None,
    ) -> AskServiceResult:
        request_run_id = run_id or generate_run_id("ask")  # API 可提前创建；CLI 不传时仍保持兼容。
        started_at = time.perf_counter()
        started_at_iso = datetime.now().astimezone().isoformat()
        bind_log_context(
            run_id=request_run_id,
            user_id=user_id,
            session_id=session_id,
        )
        logger.info("ask started")
        working_state_tracker = WorkingStateTracker(
            run_id=request_run_id,
            query=query,
            tool_call_limit=int(getattr(self.react_runner, "max_tool_calls", 8)),
            max_duration_seconds=int(getattr(self.react_runner, "max_duration_seconds", 120)),
        )
        fast_mode = should_use_fast_response(query)
        effective_extra_context = str(extra_context_instructions or "").strip()
        # 用户通过 /技能名 点名文档型 skill 时，把 SKILL.md 注入本轮上下文，确保被识别为技能指引。
        document_skill_context = self.skill_gateway.document_skill_context(query)
        if document_skill_context:
            effective_extra_context = "\n\n".join(
                part for part in (effective_extra_context, document_skill_context) if part
            )

        def emit_tracked_trace(event: TraceEvent | None) -> None:
            if event is None:
                return
            state_snapshot = working_state_tracker.apply_trace(event)
            _emit_trace(trace_callback, event)
            _emit_stream(stream_callback, "status_updated", {"state": state_snapshot})

        extracted = self._safe_extract_memories(query, user_id)
        # 前端回显的最近消息要在历史读出来之后才能去重；这次读取与下面的 prompt
        # 快照共用同一个 preparation 缓存，不会多读一次数据库。
        client_context = self._safe_client_context(
            client_messages,
            query=query,
            user_id=user_id,
            session_id=session_id,
            run_id=request_run_id,
            fast_mode=fast_mode,
        )
        if client_context:
            effective_extra_context = "\n\n".join(
                part for part in (client_context, effective_extra_context) if part
            )
        prompt_snapshot = _safe_build_prompt_snapshot(
            query=query,
            user_id=user_id,
            session_id=session_id,
            run_id=request_run_id,
            extra_context_instructions=effective_extra_context,
            enable_tools=not fast_mode,
            fast_mode=fast_mode,
        )
        prompt_event = _build_prompt_trace_event(prompt_snapshot)
        emit_tracked_trace(prompt_event)
        memory_event = _add_trace_details(_build_memory_trace_event(extracted), run_id=request_run_id)
        emit_tracked_trace(memory_event)
        try:
            plan, execution, trace = self._run_skill_or_loop(
                query=query,
                user_id=user_id,
                session_id=session_id,
                extracted_memories=extracted,
                trace_callback=emit_tracked_trace,
                stream_callback=stream_callback,
                run_id=request_run_id,
                extra_context_instructions=effective_extra_context,
                attachment_images=attachment_images,
                cancellation_check=cancellation_check,
                working_state_tracker=working_state_tracker,
            )
            if cancellation_check and cancellation_check() and execution.status != "cancelled":
                execution = _build_cancelled_execution(
                    plan=plan,
                    run_id=request_run_id,
                    execution_source=execution.execution_source,
                    observations=execution.observations,
                )
                trace = [*trace, _build_cancellation_trace_event()]
            char_limit, response_format, previous_assistant = self._safe_response_constraints(
                user_id=user_id, session_id=session_id, query=query
            )
            formatted_response = repair_format_response(
                execution.response_text,
                response_format,
                previous_assistant,
                current_query=query,
            )
            limited_response = enforce_response_char_limit(formatted_response, char_limit)
            if limited_response != execution.response_text:
                execution = replace(
                    execution,
                    response_text=limited_response,
                    citation_bundle=reanchor_bundle(limited_response, execution.citation_bundle),
                )
            duration_seconds = int(time.perf_counter() - started_at)
            final_working_state = working_state_tracker.finish(
                status=execution.status,
                stop_reason=execution.stop_reason,
                elapsed_seconds=duration_seconds,
            )
            execution = replace(execution, working_state=final_working_state)
            _emit_stream(stream_callback, "status_updated", {"state": final_working_state})
            effective_run_id = execution.run_id or request_run_id
            bind_log_context(run_id=effective_run_id, user_id=user_id, session_id=session_id)
            register_run_deliverables_quietly(
                user_id=user_id, session_id=session_id, run_id=effective_run_id, started_at_iso=started_at_iso,
                artifact_service=ArtifactService(workspace_root=user_workspace_root(user_id)),
            )
            if prompt_snapshot is not None:
                prompt_snapshot["run_id"] = effective_run_id
            # 运行循环会先发出 final_response/running，便于前端即时展示“正在整理回复”。
            # 这里必须用最终 ExecutionResult 覆盖同 key 事件并补发终态，否则 NDJSON 已结束时
            # 页面仍会残留 running。终态只更新时间线，不重复推进已经 finish 的 WorkingState。
            trace = [
                *[item for item in trace if item.key != "final"],
                _build_final_trace_event(execution),
            ]
            trace = _attach_trace_runtime_metadata(
                trace,
                run_id=effective_run_id,
                execution_source=execution.execution_source,
                error_stage=execution.error_stage,
            )
            _emit_trace(trace_callback, next(item for item in reversed(trace) if item.key == "final"))
            _emit_stream(stream_callback, "response_replace", {"text": execution.response_text})
            record_conversation_safely(
                store_factory=self.store_factory,
                user_id=user_id,
                session_id=session_id,
                user_input=query,
                assistant_output=execution.response_text,
                run_id=effective_run_id,
                suppress_user_message=suppress_user_message,
                user_message_persisted=user_message_persisted,
                attachment_ids=attachment_ids,
            )
            if stream_callback is not None:
                # 流式正文结束后立即交付终态；语义标题在有界后台队列中补齐。
                schedule_conversation_title(
                    user_id, session_id,
                    lambda: self._safe_ensure_conversation_title(user_id, session_id, query),
                )
            else:
                self._safe_ensure_conversation_title(
                    user_id=user_id, session_id=session_id, user_input=query,
                )
            self._safe_refresh_conversation_summary(user_id=user_id, session_id=session_id)
            finished_at_iso = datetime.now().astimezone().isoformat()
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            history_metadata = {
                "run_id": effective_run_id,
                "session_id": session_id,
                "response_text": execution.response_text,
                "plan": plan.to_metadata(),
                "started_at": started_at_iso,
                "finished_at": finished_at_iso,
                "duration_ms": duration_ms,
                **execution.to_metadata(),
                "trace": [item.to_metadata() for item in _prepend_trace_event(prompt_event, trace)],
                "extracted_memories": [item.__dict__ for item in extracted],
                "prompt_snapshot": prompt_snapshot,
            }
            # 模型请求体改存本地短命文件；这里再兜底剔除，避免旧路径误写进 history。
            history_metadata.pop("model_requests", None)
            self._safe_add_history(
                user_id=user_id,
                task_type="ask",
                user_input=query,
                output_summary=summarize_text(execution.response_text),
                status=execution.status,
                error_message=execution.stop_reason if execution.status in {"error", "max_iterations"} else None,
                metadata=history_metadata,
            )
            logger.info(
                "ask finished: execution_source=%s status=%s error_stage=%s duration_ms=%s",
                execution.execution_source,
                execution.status,
                execution.error_stage,
                duration_ms,
            )
            return AskServiceResult(
                query=query,
                user_id=user_id,
                session_id=session_id,
                plan=plan,
                execution=execution,
                extracted_memories=extracted,
                trace=_prepend_trace_event(prompt_event, trace),
                prompt_snapshot=prompt_snapshot,
            )
        except Exception as exc:
            error_plan = _build_error_plan(query)
            finished_at_iso = datetime.now().astimezone().isoformat()
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self._safe_add_history(
                user_id=user_id,
                task_type="ask",
                user_input=query,
                output_summary=None,
                status="error",
                error_message=str(exc),
                metadata={
                    "run_id": request_run_id,
                    "session_id": session_id,
                    "response_text": "",
                    "plan": error_plan.to_metadata(),
                    "started_at": started_at_iso,
                    "finished_at": finished_at_iso,
                    "duration_ms": duration_ms,
                    "error_stage": "service_exception",
                    "extracted_memories": [item.__dict__ for item in extracted],
                },
            )
            logger.exception(
                "ask failed: error_stage=service_exception duration_ms=%s",
                duration_ms,
            )
            raise
        finally:
            clear_log_context()

    def extract_memories(self, text: str, user_id: str = DEFAULT_USER_ID) -> list[ExtractedMemory]:
        """对外暴露一个安全的偏好/记忆抽取入口，供 remember 等命令复用。"""
        return self._safe_extract_memories(text, user_id)

    def _run_skill_or_loop(
        self,
        *,
        query: str,
        user_id: str,
        session_id: str,
        extracted_memories: list[ExtractedMemory],
        trace_callback: TraceCallback | None = None,
        stream_callback: StreamCallback | None = None,
        run_id: str,
        extra_context_instructions: str | None = None,
        attachment_images: list[Any] | None = None,
        cancellation_check: CancellationCheck | None = None,
        working_state_tracker: WorkingStateTracker | None = None,
    ) -> tuple[TaskPlan, ExecutionResult, list[TraceEvent]]:
        match = self.skill_gateway.resolve(query=query, user_id=user_id, session_id=session_id)
        if not match:
            logger.info("skill route miss，进入 Agent loop 主链路")
            execution = self.react_runner.run(
                query=query,
                user_id=user_id,
                session_id=session_id,
                progress_callback=lambda event_name, payload: _handle_executor_progress(
                    event_name=event_name,
                    payload=payload,
                    trace_callback=trace_callback,
                    stream_callback=stream_callback,
                ),
                run_id=run_id,
                extra_context_instructions=extra_context_instructions,
                attachment_images=attachment_images,
                cancellation_check=cancellation_check,
                working_state_tracker=working_state_tracker,
            )
            plan = execution.plan
            trace = _build_react_trace(plan=plan, execution=execution, extracted_memories=extracted_memories)
            return plan, execution, trace

        previous_inputs = match.session.inputs if match.session else {}
        previous_state = match.session.state if match.session else {}
        logger.info(
            "skill route hit: skill=%s tool=%s route_type=%s continuation=%s",
            match.skill.name,
            match.skill.tool_name,
            match.route_type,
            match.is_continuation,
        )

        if match.session and match.session.status == SKILL_STATUS_NEEDS_CONFIRMATION:
            normalized_query = "".join(query.split())
            if normalized_query in {"取消", "算了", "不用了", "停止"}:
                logger.info("skill confirmation canceled by user")
                self.skill_session_store.clear(user_id=user_id, session_id=session_id)
                plan = _build_skill_plan(match.skill.name, match.skill.tool_name, match.skill.description, risk_level="medium")
                _emit_trace(trace_callback, _build_skill_route_trace_event(match))
                execution = ExecutionResult(
                    status=SKILL_STATUS_SUCCESS,
                    response_text="已取消这次技能执行。",
                    plan=plan,
                    observations=[],
                    stop_reason="用户取消技能执行",
                    missing_info=[],
                    run_id=run_id,
                    execution_source=EXECUTION_SOURCE_SKILL_COMMAND,
                    error_stage="confirmation",
                )
                trace = _build_skill_trace(match=match, plan=plan, execution=execution, extracted_memories=extracted_memories)
                return plan, execution, trace
            if normalized_query not in {"确认", "确定", "继续", "好", "好的"}:
                logger.info("skill confirmation still waiting for user")
                plan = _build_skill_plan(match.skill.name, match.skill.tool_name, match.skill.description, risk_level="medium")
                _emit_trace(trace_callback, _build_skill_route_trace_event(match))
                execution = ExecutionResult(
                    status=SKILL_STATUS_NEEDS_CONFIRMATION,
                    response_text="这个技能需要你先确认。我收到“确认”后再执行；如果不做了，回复“取消”。",
                    plan=plan,
                    observations=[],
                    stop_reason="等待用户确认执行技能",
                    missing_info=[],
                    run_id=run_id,
                    execution_source=EXECUTION_SOURCE_SKILL_COMMAND,
                    error_stage="confirmation",
                )
                trace = _build_skill_trace(match=match, plan=plan, execution=execution, extracted_memories=extracted_memories)
                return plan, execution, trace

        routed_inputs = dict(match.routed_inputs or {})
        if match.session and match.session.status == SKILL_STATUS_NEEDS_INPUT:
            logger.info("skill session resumed for missing inputs")
            routed_inputs.update(_inputs_from_pending_missing_fields(query, match.skill, previous_state))

        slot_result = self.skill_slot_filler.fill(
            skill=match.skill,
            query=query,
            existing_inputs=previous_inputs,
            routed_inputs=routed_inputs,
            working_directory=str(user_workspace_root(user_id)),
        )
        if not slot_result.is_complete:
            logger.info("skill waiting for more input: missing_info=%s", "、".join(slot_result.missing_info))
            self.skill_session_store.upsert(
                user_id=user_id,
                session_id=session_id,
                skill_name=match.skill.name,
                tool_name=match.skill.tool_name,
                status=SKILL_STATUS_NEEDS_INPUT,
                inputs=slot_result.inputs,
                state={**previous_state, "phase": "awaiting_required_inputs", "missing_info": slot_result.missing_info},
                run_id=run_id,
                interaction_mode=match.skill.interaction_mode,
            )
            plan = _build_skill_plan(match.skill.name, match.skill.tool_name, match.skill.description)
            _emit_trace(trace_callback, _build_skill_route_trace_event(match))
            execution = ExecutionResult(
                status=SKILL_STATUS_NEEDS_INPUT,
                response_text=build_missing_info_prompt(skill=match.skill, missing_info=slot_result.missing_info),
                plan=plan,
                observations=[],
                stop_reason="缺少技能执行所需信息",
                missing_info=slot_result.missing_info,
                run_id=run_id,
                execution_source=EXECUTION_SOURCE_SKILL_COMMAND,
                error_stage="slot_filling",
            )
            trace = _build_skill_trace(match=match, plan=plan, execution=execution, extracted_memories=extracted_memories)
            return plan, execution, trace

        if match.skill.requires_confirmation and not match.is_continuation:
            logger.info("skill waiting for confirmation: skill=%s", match.skill.name)
            self.skill_session_store.upsert(
                user_id=user_id,
                session_id=session_id,
                skill_name=match.skill.name,
                tool_name=match.skill.tool_name,
                status=SKILL_STATUS_NEEDS_CONFIRMATION,
                inputs=slot_result.inputs,
                state={"phase": "awaiting_confirmation"},
                run_id=run_id,
                interaction_mode=match.skill.interaction_mode,
            )
            plan = _build_skill_plan(match.skill.name, match.skill.tool_name, match.skill.description, risk_level="medium")
            _emit_trace(trace_callback, _build_skill_route_trace_event(match))
            execution = ExecutionResult(
                status=SKILL_STATUS_NEEDS_CONFIRMATION,
                response_text=f"这个技能会执行敏感操作，确认后我再继续。请直接回复“确认”或“取消”。",
                plan=plan,
                observations=[],
                stop_reason="等待用户确认执行技能",
                missing_info=[],
                run_id=run_id,
                execution_source=EXECUTION_SOURCE_SKILL_COMMAND,
                error_stage="confirmation",
            )
            trace = _build_skill_trace(match=match, plan=plan, execution=execution, extracted_memories=extracted_memories)
            return plan, execution, trace

        started_at = time.perf_counter()
        running_plan = _build_skill_plan(
            match.skill.name,
            match.skill.tool_name,
            match.skill.description,
            risk_level="medium" if match.skill.requires_confirmation else "low",
        )
        if cancellation_check and cancellation_check():
            execution = _build_cancelled_execution(
                plan=running_plan,
                run_id=run_id,
                execution_source=EXECUTION_SOURCE_SKILL_COMMAND,
            )
            trace = _build_skill_trace(
                match=match,
                plan=running_plan,
                execution=execution,
                extracted_memories=extracted_memories,
            )
            return running_plan, execution, [*trace, _build_cancellation_trace_event()]
        _emit_trace(trace_callback, _build_skill_route_trace_event(match))
        _emit_trace(
            trace_callback,
            _build_running_tool_trace_event(
                index=1,
                name=f"执行技能 {match.skill.name}",
                tool=match.skill.tool_name or match.skill.name,
                inputs=slot_result.inputs,
            ),
        )
        result = self.skill_runtime.run(
            skill=match.skill,
            query=query,
            user_id=user_id,
            session_id=session_id,
            inputs=slot_result.inputs,
            state={
                **previous_state,
                "run_id": run_id,
                "confirmation_received": bool(match.session and match.session.status == SKILL_STATUS_NEEDS_CONFIRMATION),
            },
            run_id=run_id,
        )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        if cancellation_check and cancellation_check():
            execution = _build_cancelled_execution(
                plan=running_plan,
                run_id=run_id,
                execution_source=EXECUTION_SOURCE_SKILL_COMMAND,
            )
            trace = _build_skill_trace(
                match=match,
                plan=running_plan,
                execution=execution,
                extracted_memories=extracted_memories,
            )
            return running_plan, execution, [*trace, _build_cancellation_trace_event()]
        logger.info(
            "skill finished: skill=%s status=%s duration_ms=%s",
            match.skill.name,
            result.status,
            duration_ms,
        )

        if result.clear_session or result.status in {SKILL_STATUS_SUCCESS, SKILL_STATUS_ERROR}:
            self.skill_session_store.clear(user_id=user_id, session_id=session_id)
        else:
            self.skill_session_store.upsert(
                user_id=user_id,
                session_id=session_id,
                skill_name=match.skill.name,
                tool_name=match.skill.tool_name,
                status=result.status,
                inputs=result.inputs,
                state=result.state,
                run_id=run_id,
                interaction_mode=match.skill.interaction_mode,
            )

        plan = running_plan
        observation = StepObservation(
            index=1,
            name=f"执行技能 {match.skill.name}",
            tool=match.skill.tool_name or match.skill.name,
            status=result.status,
            inputs=result.inputs,
            output=result.output_text,
            error_message=result.response_text if result.status == SKILL_STATUS_ERROR else None,
            duration_ms=duration_ms,
            attempts=1,
        )
        _emit_trace(trace_callback, _observation_to_trace_event(observation))
        execution = ExecutionResult(
            status=result.status,
            response_text=result.response_text,
            plan=plan,
            observations=[observation],
            stop_reason=result.stop_reason,
            missing_info=result.missing_info,
            run_id=run_id,
            execution_source=EXECUTION_SOURCE_SKILL_COMMAND,
            error_stage="skill_runtime" if result.status == SKILL_STATUS_ERROR else None,
        )
        trace = _build_skill_trace(match=match, plan=plan, execution=execution, extracted_memories=extracted_memories)
        return plan, execution, trace
    def _safe_extract_memories(self, text: str, user_id: str) -> list[ExtractedMemory]:
        try:
            return self.memory_extractor(text, user_id)
        except DailyAgentError:
            return []

    def _safe_client_context(
        self,
        client_messages: list[Any] | None,
        *,
        query: str,
        user_id: str,
        session_id: str,
        run_id: str,
        fast_mode: bool,
    ) -> str:
        """把前端回显的最近消息压成临时上下文，并去掉已在原生历史里的部分。

        读取历史失败时退化为不去重（保留全部回显），宁多花 token 也不丢上下文。
        """
        if not client_messages:
            return ""
        # 本地即可回答的输入不会构建 prompt 快照，没必要为此读一遍数据库。
        if local_response_for_simple_input(query):
            return build_client_context_instructions(client_messages, current_query=query)
        try:
            settings = load_settings()
            history_messages = MySQLContextBuilder().build_prompt_message_context(
                user_id=user_id,
                session_id=session_id,
                token_budget=settings.context_token_budget,
                current_run_id=run_id,
                fast_mode=fast_mode,
            ).history_messages
        except Exception:
            history_messages = []
        return build_client_context_instructions(
            client_messages,
            current_query=query,
            history_messages=history_messages,
        )

    def _safe_refresh_conversation_summary(self, user_id: str, session_id: str) -> None:
        try:
            self.store_factory().refresh_conversation_summary(user_id=user_id, session_id=session_id)
        except DailyAgentError:
            # 摘要层主要是为了控上下文长度，不应影响主回答。
            pass

    def _safe_ensure_conversation_title(self, user_id: str, session_id: str, user_input: str) -> None:
        """为会话生成一次语义标题；模型或数据库失败都不能影响主回答。"""
        try:
            store = self.store_factory()
            get_title = getattr(store, "get_conversation_title", None)
            save_title = getattr(store, "save_conversation_title", None)
            # 测试替身和旧版自定义存储可能还没有标题接口，此时保持原有降级行为。
            if not callable(get_title) or not callable(save_title):
                return
            if get_title(user_id=user_id, session_id=session_id):
                return
            # 第一次模型调用失败后允许在后续问答重试，但重试时仍必须使用该会话
            # 最早的 user 消息，不能把第二轮追问误当成标题主题。
            title_seed = user_input
            list_messages = getattr(store, "list_conversation_messages", None)
            if callable(list_messages):
                stored_messages = list_messages(user_id=user_id, session_id=session_id)
                chronological_messages = reversed(stored_messages)
                title_seed = next(
                    (
                        str(getattr(message, "content", "") or "")
                        for message in chronological_messages
                        if getattr(message, "role", "") == "user"
                        and str(getattr(message, "content", "") or "").strip()
                    ),
                    user_input,
                )
            title = generate_conversation_title(title_seed, settings=self.settings)
            if title:
                save_title(
                    title,
                    user_id=user_id,
                    session_id=session_id,
                    source="local" if local_response_for_simple_input(title_seed) else "model",
                )
        except Exception:
            logger.exception("会话语义标题生成失败，已降级使用首条消息标题")

    def _safe_response_constraints(self, *, user_id: str, session_id: str, query: str) -> tuple[int | None, str | None, str]:
        """读取本会话格式约束和上一条回答；记忆库不可用时不能影响正常回答。"""
        try:
            messages = list(self.store_factory().list_conversation_messages(user_id=user_id, session_id=session_id))
            chronological = list(reversed(messages))
            previous_assistant = next(
                (str(getattr(item, "content", "") or "") for item in messages if getattr(item, "role", "") == "assistant"),
                "",
            )
            return (
                extract_response_char_limit(chronological, current_query=query),
                extract_response_format(chronological, current_query=query),
                previous_assistant,
            )
        except Exception:
            return extract_response_char_limit([], current_query=query), extract_response_format([], current_query=query), ""

    def _safe_add_history(
        self,
        user_id: str,
        task_type: str,
        user_input: str,
        output_summary: str | None,
        status: str,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.store_factory().add_task_history(
                task_type=task_type,
                user_input=user_input,
                output_summary=output_summary,
                status=status,
                error_message=error_message,
                metadata=metadata,
                user_id=user_id,
            )
        except DailyAgentError:
            pass


def _build_skill_plan(skill_name: str, tool_name: str | None, description: str, risk_level: str = "low") -> TaskPlan:
    """给新技能链路构造一份兼容现有 API / CLI 的 TaskPlan。"""
    return TaskPlan(
        intent=f"skill:{skill_name}",
        steps=[
            PlanStep(
                name=f"执行技能 {skill_name}",
                tool=tool_name or skill_name,
                reason=description or "命中技能路由后，交给对应 handler 处理。",
            )
        ],
        missing_info=[],
        risk_level=risk_level,
    )


def _build_error_plan(query: str) -> TaskPlan:
    intent = "empty" if not query.strip() else "error"
    return TaskPlan(
        intent=intent,
        steps=[
            PlanStep(
                name="返回结构化错误",
                tool=None,
                reason="主流程发生未恢复异常，直接暴露错误阶段，避免生成看似成功的兜底回复。",
            )
        ],
        missing_info=[],
        risk_level="low",
    )


def _inputs_from_pending_missing_fields(query: str, skill: Any, state: dict[str, Any]) -> dict[str, Any]:
    if state.get("phase") != "awaiting_required_inputs":
        return {}
    missing_info = state.get("missing_info")
    if not isinstance(missing_info, list) or len(missing_info) != 1:
        return {}
    field_name = str(missing_info[0])
    field_meta = _skill_field_meta(skill, field_name)
    if _field_looks_like_date(field_name, field_meta) or not _field_accepts_raw_text(field_name, field_meta):
        return {}
    value = query.strip()
    if not value:
        return {}
    return {field_name: value}


def _skill_properties(skill: Any) -> dict[str, Any]:
    schema = getattr(skill, "input_schema", None)
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return properties


def _skill_field_meta(skill: Any, field_name: str) -> dict[str, Any]:
    properties = _skill_properties(skill)
    meta = properties.get(field_name)
    if not isinstance(meta, dict):
        return {}
    return meta


def _field_looks_like_date(field_name: str, meta: dict[str, Any]) -> bool:
    description = str(meta.get("description") or "")
    title = str(meta.get("title") or "")
    normalized_name = field_name.lower()
    return normalized_name in {"date", "target_date", "day", "departure_date", "return_date"} or "日期" in description or "日期" in title


def _field_accepts_raw_text(field_name: str, meta: dict[str, Any]) -> bool:
    normalized_name = field_name.lower()
    if normalized_name in {"city", "text", "content", "message", "author", "name"}:
        return True
    description = str(meta.get("description") or "")
    title = str(meta.get("title") or "")
    return "文本" in description or "文本" in title


def _emit_trace(callback: TraceCallback | None, event: TraceEvent | None) -> None:
    if callback and event:
        callback(event)


def _emit_stream(callback: StreamCallback | None, event_type: str, payload: dict[str, Any]) -> None:
    if callback:
        callback(event_type, payload)


def _handle_executor_progress(
    *,
    event_name: str,
    payload: dict[str, Any],
    trace_callback: TraceCallback | None,
    stream_callback: StreamCallback | None,
) -> None:
    if event_name == "response_delta":
        stream_payload: dict[str, str] = {"delta": str(payload.get("delta") or "")}
        if payload.get("text") is not None:
            stream_payload["text"] = str(payload.get("text") or "")
        _emit_stream(stream_callback, "response_delta", stream_payload)
        return
    if event_name == "response_block":
        _emit_stream(stream_callback, "response_block", dict(payload))
        return
    _emit_trace(trace_callback, executor_progress_to_trace_event(event_name, payload))


def _prepend_trace_event(event: TraceEvent | None, trace: list[TraceEvent]) -> list[TraceEvent]:
    if not event:
        return trace
    return [event, *[item for item in trace if item.key != event.key]]


def _attach_trace_runtime_metadata(
    trace: list[TraceEvent],
    *,
    run_id: str,
    execution_source: str,
    error_stage: str | None,
) -> list[TraceEvent]:
    decorated: list[TraceEvent] = []
    for event in trace:
        details = dict(event.details)
        details.setdefault("run_id", run_id)
        details.setdefault("execution_source", execution_source)
        if error_stage:
            details.setdefault("error_stage", error_stage)
        decorated.append(
            TraceEvent(
                key=event.key,
                stage=event.stage,
                status=event.status,
                title=event.title,
                summary=event.summary,
                details=details,
            )
        )
    return decorated


def _add_trace_details(event: TraceEvent | None, **extra_details: Any) -> TraceEvent | None:
    if event is None:
        return None
    details = dict(event.details)
    for key, value in extra_details.items():
        if value is not None:
            details.setdefault(key, value)
    return TraceEvent(
        key=event.key,
        stage=event.stage,
        status=event.status,
        title=event.title,
        summary=event.summary,
        details=details,
    )


def _safe_build_prompt_snapshot(
    query: str,
    user_id: str,
    session_id: str,
    run_id: str,
    extra_context_instructions: str | None = None,
    enable_tools: bool = True,
    fast_mode: bool = False,
) -> dict[str, Any]:
    # 本地可回答的输入不应为诊断快照枚举 MCP、扫描技能和读取完整上下文。
    # 实际 Runner 仍优先检查待确认任务，问候不能绕过原有续接与确认流程。
    if local_response_for_simple_input(query):
        return PromptSnapshot(
            user_id=user_id, session_id=session_id, run_id=run_id,
            model_id="local", context_token_budget=0, enable_tools=False,
            tool_names=[], skill_names=[], instructions=[], input_messages=[],
            cache_epoch="local", summarized_message_count=0, user_input=query,
            notes=["本地简单输入预检快照；存在待续接任务时仍由 Runner 按原流程处理。"],
        ).to_metadata()
    try:
        return build_prompt_snapshot(
            query=query,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            extra_context_instructions=extra_context_instructions,
            enable_tools=enable_tools,
            fast_mode=fast_mode,
        ).to_metadata()
    except Exception as exc:
        return {
            "user_id": user_id,
            "session_id": session_id,
            "run_id": run_id,
            "model_id": "unknown",
            "context_token_budget": None,
            "enable_tools": enable_tools,
            "tool_names": [],
            "skill_names": [],
            "instructions": [],
            "user_input": query,
            "prompt_text": query,
            "notes": [f"生成 Prompt 快照失败：{exc}"],
        }


def _build_prompt_trace_event(prompt_snapshot: dict[str, Any] | None) -> TraceEvent | None:
    if not prompt_snapshot:
        return None
    instructions = prompt_snapshot.get("instructions")
    tool_names = prompt_snapshot.get("tool_names")
    skill_names = prompt_snapshot.get("skill_names")
    return TraceEvent(
        key="prompt_ready",
        stage="prompt_ready",
        status="success",
        title="准备模型 Prompt",
        summary="已生成本轮发送给模型的提示词快照，可在开发者详情里查看。",
        details={
            "run_id": prompt_snapshot.get("run_id"),
            "model_id": prompt_snapshot.get("model_id"),
            "context_token_budget": prompt_snapshot.get("context_token_budget"),
            "instruction_count": len(instructions) if isinstance(instructions, list) else 0,
            "tool_count": len(tool_names) if isinstance(tool_names, list) else 0,
            "skill_count": len(skill_names) if isinstance(skill_names, list) else 0,
            "prompt_snapshot": prompt_snapshot,
        },
    )


def _build_react_route_trace_event(execution: ExecutionResult | None = None) -> TraceEvent:
    if execution and execution.execution_source == EXECUTION_SOURCE_LOCAL:
        return TraceEvent(
            key="loop_started",
            stage="loop_started",
            status="success",
            title="本地快捷回答",
            summary="这是本地可判定的简单问题，没有进入模型工具调用循环。",
            details={"route": "local"},
        )
    if execution and execution.execution_source == EXECUTION_SOURCE_ERROR:
        return TraceEvent(
            key="loop_started",
            stage="error",
            status="error",
            title="Agent loop 未启动",
            summary=execution.stop_reason,
            details={"route": "error", "error_stage": execution.error_stage},
        )
    return TraceEvent(
        key="loop_started",
        stage="loop_started",
        status="success",
        title="进入 Agent loop",
        summary="没有命中命令式技能，交给模型在工具调用循环里自主选工具并作答。",
        details={"route": EXECUTION_SOURCE_AGENT_LOOP},
    )


def _build_skill_route_trace_event(match: SkillRouteMatch) -> TraceEvent:
    return TraceEvent(
        key="loop_started",
        stage="loop_started",
        status="success",
        title="命令式技能入口",
        summary=_build_skill_route_summary(match),
        details={
            "route": EXECUTION_SOURCE_SKILL_COMMAND,
            "skill_name": match.skill.name,
            "tool_name": match.skill.tool_name,
            "trigger": match.trigger,
            "route_type": match.route_type,
            "route_reason": match.route_reason,
            "is_continuation": match.is_continuation,
            "requires_confirmation": match.skill.requires_confirmation,
            "interaction_mode": match.skill.interaction_mode,
        },
    )


def _build_input_trace_event(execution: ExecutionResult) -> TraceEvent | None:
    if execution.missing_info:
        return TraceEvent(
            key="input",
            stage="waiting_input",
            status=execution.status,
            title="等待补充信息",
            summary=f"还缺少 { '、'.join(execution.missing_info) }，本轮先暂停。",
            details={"missing_info": list(execution.missing_info)},
        )
    if execution.status == SKILL_STATUS_NEEDS_CONFIRMATION:
        return TraceEvent(
            key="input",
            stage="waiting_confirmation",
            status=execution.status,
            title="等待执行确认",
            summary="当前技能涉及敏感动作，等待用户明确确认。",
            details={"stop_reason": execution.stop_reason},
        )
    return None


def _build_running_tool_trace_event(index: int, name: str, tool: str | None, inputs: dict[str, Any]) -> TraceEvent:
    return TraceEvent(
        key=f"tool-{index}-started",
        stage="tool_started",
        status="running",
        title=name,
        summary="正在调用工具并等待返回结果。",
        details={"tool": tool, "inputs": dict(inputs), "iteration": index},
    )


def _build_final_trace_event(execution: ExecutionResult) -> TraceEvent:
    stage = "error" if execution.status == SKILL_STATUS_ERROR else "final_response"
    return TraceEvent(
        key="final",
        stage=stage,
        status=execution.status,
        title="生成回复",
        summary=_build_final_trace_summary(execution),
        details={
            "stop_reason": execution.stop_reason,
            "response_preview": summarize_text(execution.response_text, 180),
        },
    )


def _build_react_trace(
    *,
    plan: TaskPlan,
    execution: ExecutionResult,
    extracted_memories: list[ExtractedMemory],
) -> list[TraceEvent]:
    trace: list[TraceEvent] = []
    memory_event = _build_memory_trace_event(extracted_memories)
    if memory_event:
        trace.append(memory_event)
    trace.append(_build_react_route_trace_event(execution))
    trace.extend(_build_execution_trace_events(execution))
    return trace


def _build_skill_trace(
    *,
    match: SkillRouteMatch,
    plan: TaskPlan,
    execution: ExecutionResult,
    extracted_memories: list[ExtractedMemory],
) -> list[TraceEvent]:
    trace: list[TraceEvent] = []
    memory_event = _build_memory_trace_event(extracted_memories)
    if memory_event:
        trace.append(memory_event)
    trace.append(_build_skill_route_trace_event(match))
    trace.extend(_build_execution_trace_events(execution))
    return trace


def _build_execution_trace_events(execution: ExecutionResult) -> list[TraceEvent]:
    trace: list[TraceEvent] = []
    input_event = _build_input_trace_event(execution)
    if input_event:
        trace.append(input_event)
    trace.extend(chronological_execution_trace(execution.loop_events, execution.observations))
    trace.append(_build_final_trace_event(execution))
    return trace


def _build_memory_trace_event(extracted_memories: list[ExtractedMemory]) -> TraceEvent | None:
    if not extracted_memories:
        return None
    return TraceEvent(
        key="memory",
        stage="memory",
        status="success",
        title="提取用户记忆",
        summary=f"从本轮输入里抽取到 {len(extracted_memories)} 条偏好或长期记忆。",
        details={
            "items": [
                {
                    "key": item.key,
                    "value": item.value,
                    "memory_text": item.memory_text,
                    "aliases": dict(item.aliases),
                    "kind": item.kind,
                    "action": item.action,
                    "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                }
                for item in extracted_memories
            ]
        },
    )


def _build_skill_route_summary(match: SkillRouteMatch) -> str:
    if match.is_continuation:
        return f"检测到会话里有未完成的 {match.skill.name} 技能，继续上一次执行。"
    if match.trigger:
        return f"通过触发词 {match.trigger} 命中技能 {match.skill.name}。"
    if match.route_reason:
        return f"根据路由判断命中技能 {match.skill.name}：{match.route_reason}"
    return f"命中技能 {match.skill.name}，交给对应 handler 执行。"
