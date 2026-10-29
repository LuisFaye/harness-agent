"""驱动 Agno 的模型工具循环，并把流式事件映射为项目既有的执行结果和时间线。

模型负责自主选择 Tool、观察结果和继续决策；本模块负责协议适配、暂停确认、
循环保护及结构化失败，不通过规则伪造模型或工具的实时结果。
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, replace
import time
from typing import Any, Callable
from agno.exceptions import RunCancelledException
from daily_agent.agent import build_agent, build_fast_agent
from daily_agent.agent_event_stream import stream_agent_continuation, stream_agent_run
from daily_agent.errors import DailyAgentError, DependencyError, MissingConfigError
from daily_agent.executor import (
    ERROR,
    EXECUTION_SOURCE_AGENT_LOOP,
    EXECUTION_SOURCE_ERROR,
    EXECUTION_SOURCE_LOCAL,
    MAX_ITERATIONS,
    SUCCESS,
    ExecutionResult,
    StepObservation,
)
from daily_agent.evidence import DOCUMENT_INLINE_TOKEN_LIMIT
from daily_agent.loop_control import AgentLoopControl, loop_guard_reason
from daily_agent.loop_outcomes import (
    missing_tool_call_result,
    needs_input_result,
    query_accepts_unavailable_result,
    should_report_missing_tool_call,
    should_return_needs_input,
)
from daily_agent.model_request_capture import (
    capturing_model_requests,
    snapshot_captured_model_requests,
)
from daily_agent.planner import PlanStep, TaskPlan
from daily_agent.runtime_state import (
    DEFAULT_RUNTIME_STATE_TTL_HOURS,
    JsonStateFile,
    REACT_PAUSE_LEGACY_PATH,
    RuntimeStateRepository,
    build_state_expiry,
    generate_run_id,
)
from daily_agent.react_runner_helpers import (
    accumulate_usage_from_run_output as _accumulate_usage_from_run_output,
    confirmation_note as _confirmation_note,
    confirm_run_requirements as _confirm_run_requirements,
    format_cache_usage,
    is_paused_run_output as _is_paused_run_output,
    is_run_output_object as _is_run_output_object,
    observation_from_tool as _observation_from_tool,
    pending_run_from_payload as _pending_run_from_payload,
    pending_run_from_record as _pending_run_from_record,
    response_acknowledges_tool_failure as _response_acknowledges_tool_failure,
    synthesize_plan as _synthesize_plan,
    tool_args as _tool_args,
    tool_event_signature as _tool_event_signature,
    tool_key as _tool_key,
    tool_name as _tool_name,
)
from daily_agent.react_runner_models import PendingReactRun
from daily_agent.services.rendering import sanitize_reasoning_text, sanitize_response_text
from daily_agent.search_policy import official_search_failure_response, official_search_verification_failed
from daily_agent.working_state import WorkingStateTracker
from daily_agent.database_connections import apply_database_interaction_result
from daily_agent.citations.answer_stream import AgentAnswerStreamParser, finish_answer_stream, join_answer_blocks
from daily_agent.citations.composer import compose_agent_citations
from daily_agent.citations.models import AgentAnswerBlock, CitationBundle
from daily_agent.react_text import (
    coerce_text as _coerce_text,
    compact_loop_text as _compact_loop_text,
    forced_format_retry_query as _forced_format_retry_query,
    forced_tool_retry_query as _forced_tool_retry_query,
    friendly_tool_name as _friendly_tool_name,
    is_structured_format_failure as _is_structured_format_failure,
    is_transient_model_error as _is_transient_model_error,
    local_response_for_simple_input as _local_response_for_simple_input,
    format_relative_date_answer as _format_relative_date_answer,
    normalize_event_type as _normalize_event_type,
)
from daily_agent.response_route import should_use_fast_response
NEEDS_CONFIRMATION = "needs_confirmation"
AgentFactory = Callable[..., Any]
ProgressCallback = Callable[[str, dict[str, Any]], None]
CancellationCheck = Callable[[], bool]
logger = logging.getLogger(__name__)
class ReactPauseStore:
    def __init__(
        self,
        path=None,
        state_repository: RuntimeStateRepository | None = None,
        ttl_hours: int = DEFAULT_RUNTIME_STATE_TTL_HOURS,
    ) -> None:
        self.path = path.resolve() if path else None
        self.file_store = JsonStateFile(self.path or REACT_PAUSE_LEGACY_PATH)
        self.state_repository = None if path else (state_repository or RuntimeStateRepository())
        self.ttl_hours = max(1, int(ttl_hours))
    def get(self, *, user_id: str, session_id: str):
        record = self.get_record(user_id=user_id, session_id=session_id)
        return record.run_output if record else None
    def get_record(self, *, user_id: str, session_id: str) -> PendingReactRun | None:
        if self.state_repository is None:
            return self._get_from_file(user_id=user_id, session_id=session_id)
        try:
            record = self.state_repository.get_react_pause_state(user_id=user_id, session_id=session_id)
            if record:
                return _pending_run_from_record(record)
            migrated = self._get_from_file(user_id=user_id, session_id=session_id)
            if migrated:
                self.save(
                    user_id=user_id,
                    session_id=session_id,
                    run_output=migrated.run_output,
                    run_id=migrated.run_id,
                )
                self.file_store.clear(user_id=user_id, session_id=session_id)
            return migrated
        except DailyAgentError:
            return self._get_from_file(user_id=user_id, session_id=session_id)
    def save(self, *, user_id: str, session_id: str, run_output: Any, run_id: str | None = None) -> None:
        if not hasattr(run_output, "to_dict"):
            return
        payload = run_output.to_dict()
        effective_run_id = str(run_id or getattr(run_output, "run_id", "") or generate_run_id("react"))
        if self.state_repository is None:
            self.file_store.upsert(
                user_id=user_id,
                session_id=session_id,
                payload={
                    "user_id": user_id,
                    "session_id": session_id,
                    "run_id": effective_run_id,
                    "status": NEEDS_CONFIRMATION,
                    "payload": payload,
                },
            )
            return
        try:
            self.state_repository.upsert_react_pause_state(
                user_id=user_id,
                session_id=session_id,
                run_id=effective_run_id,
                status=NEEDS_CONFIRMATION,
                payload=payload,
                expires_at=build_state_expiry(self.ttl_hours),
            )
        except DailyAgentError:
            self.file_store.upsert(
                user_id=user_id,
                session_id=session_id,
                payload={
                    "user_id": user_id,
                    "session_id": session_id,
                    "run_id": effective_run_id,
                    "status": NEEDS_CONFIRMATION,
                    "payload": payload,
                },
            )
    def clear(self, *, user_id: str, session_id: str) -> bool:
        if self.state_repository is None:
            return self.file_store.clear(user_id=user_id, session_id=session_id)
        cleared = False
        try:
            cleared = self.state_repository.clear_react_pause_state(user_id=user_id, session_id=session_id)
        except DailyAgentError:
            pass
        file_cleared = self.file_store.clear(user_id=user_id, session_id=session_id)
        return cleared or file_cleared
    def _get_from_file(self, *, user_id: str, session_id: str) -> PendingReactRun | None:
        try:
            raw = self.file_store.get(user_id=user_id, session_id=session_id)
        except DailyAgentError:
            return None
        if not isinstance(raw, dict):
            return None
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
        status = str(raw.get("status") or NEEDS_CONFIRMATION)
        run_id = str(raw.get("run_id") or payload.get("run_id") or generate_run_id("react"))
        return _pending_run_from_payload(
            payload=payload,
            run_id=run_id,
            status=status,
            updated_at=str(raw.get("updated_at") or ""),
            expires_at=str(raw.get("expires_at")) if raw.get("expires_at") is not None else None,
        )
class AgentLoopRunner:
    """用单一 Agno Agent 循环驱动「理解 -> 自主调工具 -> 回答」。"""
    def __init__(
        self,
        agent_factory: AgentFactory | None = None,
        max_tool_calls: int = 8,
        max_duration_seconds: float = 120,
        max_inline_tokens: int = DOCUMENT_INLINE_TOKEN_LIMIT,
        pause_store: ReactPauseStore | None = None,
    ) -> None:
        self.agent_factory = agent_factory or build_agent
        self.fast_agent_factory = agent_factory or build_fast_agent
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.max_duration_seconds = max(0.0, float(max_duration_seconds))
        self.max_inline_tokens = max(1, int(max_inline_tokens))
        self.pause_store = pause_store or ReactPauseStore()
    def run(
        self,
        query: str,
        user_id: str = "default",
        session_id: str = "default",
        progress_callback: ProgressCallback | None = None,
        run_id: str | None = None,
        extra_context_instructions: str | None = None,
        attachment_images: list[Any] | None = None,
        cancellation_check: CancellationCheck | None = None,
        working_state_tracker: WorkingStateTracker | None = None,
    ) -> ExecutionResult:
        """执行主循环，并在窗口内截获发给模型的 OpenAI 兼容请求体。"""
        with capturing_model_requests():
            result = self._run_uncaptured(
                query=query,
                user_id=user_id,
                session_id=session_id,
                progress_callback=progress_callback,
                run_id=run_id,
                extra_context_instructions=extra_context_instructions,
                attachment_images=attachment_images,
                cancellation_check=cancellation_check,
                working_state_tracker=working_state_tracker,
            )
            # 必须在 with 内 snapshot，否则 ContextVar 复位后读不到本轮记录。
            return replace(result, model_requests=snapshot_captured_model_requests())

    def _run_uncaptured(
        self,
        query: str,
        user_id: str = "default",
        session_id: str = "default",
        progress_callback: ProgressCallback | None = None,
        run_id: str | None = None,
        extra_context_instructions: str | None = None,
        attachment_images: list[Any] | None = None,
        cancellation_check: CancellationCheck | None = None,
        working_state_tracker: WorkingStateTracker | None = None,
    ) -> ExecutionResult:
        pending_record = self.pause_store.get_record(user_id=user_id, session_id=session_id)
        # HTTP 请求已经分配 run_id 时必须优先使用，避免事件流编号和 Agno 编号分叉。
        effective_run_id = run_id or (pending_record.run_id if pending_record else generate_run_id("ask"))
        if pending_record is not None:
            logger.info("agent loop continue requested: pending_status=%s", pending_record.status)
            return self._continue_or_wait(
                pending_run=pending_record.run_output,
                query=query,
                user_id=user_id,
                session_id=session_id,
                progress_callback=progress_callback,
                run_id=effective_run_id,
                extra_context_instructions=extra_context_instructions,
                cancellation_check=cancellation_check,
            )
        if cancellation_check and cancellation_check():
            return self._cancelled_result(run_id=effective_run_id)
        local_response = _local_response_for_simple_input(query)
        if local_response:
            logger.info("agent loop local shortcut answered without model")
            return self._build_result(
                status=SUCCESS,
                response_text=local_response,
                observations=[],
                stop_reason="本地处理简单请求",
                run_id=effective_run_id,
                execution_source=EXECUTION_SOURCE_LOCAL,
            )
        loop_control = AgentLoopControl(
            self.max_tool_calls,
            self.max_duration_seconds,
            user_query=query,
            run_id=effective_run_id,
            user_id=user_id,
            session_id=session_id,
            working_state_tracker=working_state_tracker,
            document_inline_token_limit=self.max_inline_tokens,
        )
        try:
            vision_images = list(attachment_images or [])
            fast_response = should_use_fast_response(query) and not vision_images
            selected_factory = self.fast_agent_factory if fast_response else self.agent_factory
            logger.info("agent loop route selected: mode=%s", "fast" if fast_response else "full")
            agent = selected_factory(
                user_id=user_id,
                session_id=session_id,
                run_id=effective_run_id,
                query=query,
                extra_context_instructions=extra_context_instructions,
                tool_call_limit=self.max_tool_calls,
                tool_hooks=[loop_control.tool_hook],
            )
        except MissingConfigError as exc:
            logger.warning("agent loop cannot start: reason=model_config")
            return self._error_result(
                message=str(exc),
                stop_reason="模型配置不可用",
                run_id=effective_run_id,
                error_stage="model_config",
            )
        except DependencyError as exc:
            logger.warning("agent loop cannot start: reason=dependency")
            return self._error_result(
                message=str(exc),
                stop_reason="运行依赖不可用",
                run_id=effective_run_id,
                error_stage="dependency",
            )
        except Exception as exc:
            logger.exception("agent loop factory failed")
            return self._error_result(
                message=f"模型运行时初始化失败：{exc}",
                stop_reason="模型运行时初始化失败",
                run_id=effective_run_id,
                error_stage="model_runtime",
            )
        try:
            logger.info("agent loop started")
            run_kwargs: dict[str, Any] = {
                "stream": True,
                "stream_events": True,
                "user_id": user_id,
                "session_id": session_id,
                "run_id": effective_run_id,
                "yield_run_output": True,
                "cancellation_check": cancellation_check,
            }
            if vision_images:
                run_kwargs["images"] = vision_images
            events = stream_agent_run(agent, query, **run_kwargs)
            usage: dict[str, Any] = {}
            structured_answer = bool(getattr(agent, "output_schema", None))
            result = self._run_loop(
                events,
                user_id,
                session_id,
                progress_callback,
                run_id=effective_run_id,
                loop_control=loop_control,
                query=query,
                cancellation_check=cancellation_check,
                usage=usage,
                structured_answer=structured_answer,
            )
            result = apply_database_interaction_result(result)
            # 只有从未触发工具的失败才允许历史兼容重试，避免写文件等操作重复执行。
            if (result.error_stage == "model_runtime"
                    and not loop_control.tool_call_count
                    and result.stop_reason == "模型执行出错"
                    and _is_transient_model_error(result.response_text)):
                logger.warning("agent model runtime returned a transient error, retrying once")
                retry_events = stream_agent_run(
                    agent,
                    query,
                    stream=True,
                    stream_events=True,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=effective_run_id,
                    yield_run_output=True,
                    cancellation_check=cancellation_check,
                )
                # 外层重试是同一轮 ask 的真实执行，usage 继续累加，避免重复或丢失。
                result = self._run_loop(
                    retry_events,
                    user_id,
                    session_id,
                    progress_callback,
                    run_id=effective_run_id,
                    loop_control=loop_control,
                    query=query,
                    cancellation_check=cancellation_check,
                    usage=usage,
                    structured_answer=structured_answer,
                )
            if (structured_answer
                    and result.error_stage == "model_runtime"
                    and not loop_control.tool_call_count
                    and _is_structured_format_failure(result.response_text)):
                logger.warning("agent loop structured answer format failed, retrying once with format constraint")
                retry_events = stream_agent_run(
                    agent,
                    _forced_format_retry_query(query),
                    stream=True,
                    stream_events=True,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=effective_run_id,
                    yield_run_output=True,
                    cancellation_check=cancellation_check,
                )
                result = self._run_loop(
                    retry_events,
                    user_id,
                    session_id,
                    progress_callback,
                    run_id=effective_run_id,
                    loop_control=loop_control,
                    query=query,
                    cancellation_check=cancellation_check,
                    usage=usage,
                    structured_answer=structured_answer,
                )
            if should_return_needs_input(query, result):
                result = needs_input_result(result)
            elif should_report_missing_tool_call(query, result):
                logger.warning("agent loop tool selection missed, retrying once with a freshness constraint")
                retry_events = stream_agent_run(
                    agent,
                    _forced_tool_retry_query(query),
                    stream=True,
                    stream_events=True,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=effective_run_id,
                    yield_run_output=True,
                    cancellation_check=cancellation_check,
                )
                result = self._run_loop(
                    retry_events,
                    user_id,
                    session_id,
                    progress_callback,
                    run_id=effective_run_id,
                    loop_control=loop_control,
                    query=query,
                    cancellation_check=cancellation_check,
                    usage=usage,
                    structured_answer=structured_answer,
                )
                if should_return_needs_input(query, result):
                    result = needs_input_result(result)
                elif should_report_missing_tool_call(query, result):
                    logger.warning("agent loop tool selection failed after forced retry")
                    result = missing_tool_call_result(query, result)
            if result.status != NEEDS_CONFIRMATION:
                self.pause_store.clear(user_id=user_id, session_id=session_id)
            return result
        except RunCancelledException:
            logger.info("agent loop cancelled: run_id=%s", effective_run_id)
            return self._cancelled_result(run_id=effective_run_id)
        except Exception as exc:
            logger.exception("agent loop failed")
            return self._error_result(
                message=f"模型工具循环执行失败：{exc}",
                stop_reason="模型工具循环执行失败",
                run_id=effective_run_id,
                error_stage="model_runtime",
            )
        finally:
            loop_control.citation_page_prefetcher.close()
    def _continue_or_wait(
        self,
        *,
        pending_run: Any,
        query: str,
        user_id: str,
        session_id: str,
        progress_callback: ProgressCallback | None,
        run_id: str,
        extra_context_instructions: str | None = None,
        cancellation_check: CancellationCheck | None = None,
    ) -> ExecutionResult:
        normalized_query = "".join(str(query or "").strip().lower().split())
        if normalized_query in {"取消", "算了", "不用了", "停止"}:
            logger.info("agent loop confirmation canceled by user")
            self.pause_store.clear(user_id=user_id, session_id=session_id)
            return self._build_result(
                status=SUCCESS,
                response_text="已取消这次工具执行。",
                observations=[],
                stop_reason="用户取消工具执行",
                run_id=run_id,
                execution_source=EXECUTION_SOURCE_AGENT_LOOP,
            )
        if normalized_query not in {"确认", "确定", "继续", "好", "好的"}:
            logger.info("agent loop confirmation still waiting for user")
            return self._build_result(
                status=NEEDS_CONFIRMATION,
                response_text="上一次工具调用还在等待确认。请回复“确认”继续执行，或回复“取消”终止。",
                observations=[],
                stop_reason="等待用户确认执行高风险操作",
                run_id=run_id,
                execution_source=EXECUTION_SOURCE_AGENT_LOOP,
                error_stage="confirmation",
            )
        if cancellation_check and cancellation_check():
            return self._cancelled_result(run_id=run_id)
        loop_control = AgentLoopControl(
            self.max_tool_calls,
            self.max_duration_seconds,
            user_query=query,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            document_inline_token_limit=self.max_inline_tokens,
        )
        try:
            agent = self.agent_factory(
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
                extra_context_instructions=extra_context_instructions,
                tool_call_limit=self.max_tool_calls,
                tool_hooks=[loop_control.tool_hook],
            )
        except MissingConfigError as exc:
            logger.warning("agent loop resume cannot start: reason=model_config")
            return self._error_result(
                message=str(exc),
                stop_reason="模型配置不可用，无法继续上次暂停的工具调用",
                run_id=run_id,
                error_stage="model_config",
            )
        except DependencyError as exc:
            logger.warning("agent loop resume cannot start: reason=dependency")
            return self._error_result(
                message=str(exc),
                stop_reason="运行依赖不可用，无法继续上次暂停的工具调用",
                run_id=run_id,
                error_stage="dependency",
            )
        except Exception as exc:
            logger.exception("agent loop resume factory failed")
            return self._error_result(
                message=f"模型运行时初始化失败：{exc}",
                stop_reason="模型运行时初始化失败",
                run_id=run_id,
                error_stage="model_runtime",
            )
        _confirm_run_requirements(pending_run)
        try:
            logger.info("agent loop continue started")
            events = stream_agent_continuation(
                agent,
                run_response=pending_run,
                run_id=run_id,
                stream=True,
                stream_events=True,
                user_id=user_id,
                session_id=session_id,
                yield_run_output=True,
                cancellation_check=cancellation_check,
            )
            usage: dict[str, Any] = {}
            result = self._run_loop(
                events,
                user_id,
                session_id,
                progress_callback,
                run_id=run_id,
                loop_control=loop_control,
                query=query,
                cancellation_check=cancellation_check,
                usage=usage,
                structured_answer=bool(getattr(agent, "output_schema", None)),
            )
            if result.status != NEEDS_CONFIRMATION:
                self.pause_store.clear(user_id=user_id, session_id=session_id)
            return result
        except RunCancelledException:
            logger.info("agent loop continue cancelled: run_id=%s", run_id)
            return self._cancelled_result(run_id=run_id)
        except Exception as exc:
            logger.exception("agent loop continue failed")
            return self._error_result(
                message=f"模型工具循环继续执行失败：{exc}",
                stop_reason="模型工具循环继续执行失败",
                run_id=run_id,
                error_stage="model_runtime",
            )
        finally:
            loop_control.citation_page_prefetcher.close()
    def _run_loop(
        self,
        events: Any,
        user_id: str,
        session_id: str,
        progress_callback: ProgressCallback | None,
        run_id: str,
        loop_control: AgentLoopControl,
        query: str,
        cancellation_check: CancellationCheck | None = None,
        usage: dict[str, Any] | None = None,
        structured_answer: bool = False,
    ) -> ExecutionResult:
        observations: list[StepObservation] = []
        content_parts: list[str] = []
        final_content: Any = None
        answer_parser = AgentAnswerStreamParser() if structured_answer else None
        agent_answer_blocks: list[AgentAnswerBlock] = []
        final_run_output: Any = None
        pending_starts: dict[str, float] = {}
        confirmation_note: str | None = None
        paused_run_output: Any = None
        error_message: str | None = None
        response_start_emitted = False
        reasoning_parts: list[str] = []
        reasoning_segment = 0
        reasoning_segment_active = False
        reasoning_segment_parts: list[str] = []
        last_reasoning_progress_length = 0
        loop_events: list[dict[str, Any]] = []
        iteration_by_tool_key: dict[str, int] = {}
        completed_tool_signatures: set[str] = set()
        pending_evaluation: int | None = None
        next_iteration = 1
        # 本轮实际发生过的模型请求用量，由调用方跨重试/跨继续累加。
        effective_usage = usage if usage is not None else {}
        def emit_loop_event(event_name: str, payload: dict[str, Any]) -> None:
            loop_events.append({"event": event_name, **payload})
            if progress_callback:
                progress_callback(event_name, payload)
        def evaluate_pending(decision: str, reason: str) -> None:
            nonlocal pending_evaluation
            if pending_evaluation is None:
                return
            emit_loop_event(
                "iteration_evaluated",
                {"iteration": pending_evaluation, "decision": decision, "reason": reason},
            )
            pending_evaluation = None
        def start_reasoning() -> None:
            nonlocal reasoning_segment, reasoning_segment_active, last_reasoning_progress_length
            if reasoning_segment_active:
                return
            reasoning_segment += 1
            reasoning_segment_active = True
            reasoning_segment_parts.clear()
            last_reasoning_progress_length = 0
            emit_loop_event(
                "reasoning_started",
                {"model_mode": "thinking", "segment": reasoning_segment},
            )
        def segment_reasoning_text() -> tuple[str, bool]:
            return sanitize_reasoning_text("".join(reasoning_segment_parts))
        def update_reasoning_progress(*, force: bool = False) -> None:
            nonlocal last_reasoning_progress_length
            if not reasoning_segment_active:
                return
            content, truncated = segment_reasoning_text()
            if not content.strip():
                return
            if not force and len(content) - last_reasoning_progress_length < 48:
                return
            delta = content[last_reasoning_progress_length:]
            if not force and not delta:
                return
            last_reasoning_progress_length = len(content)
            if progress_callback:
                progress_callback(
                    "reasoning_progress",
                    {
                        "segment": reasoning_segment,
                        "delta": delta,
                        "content": content,
                        "character_count": len("".join(reasoning_parts)),
                        "truncated": truncated,
                    },
                )
        def finish_reasoning() -> None:
            nonlocal reasoning_segment_active, last_reasoning_progress_length
            if not reasoning_segment_active:
                return
            content, truncated = segment_reasoning_text()
            if not content.strip():
                reasoning_segment_active = False
                last_reasoning_progress_length = 0
                return
            update_reasoning_progress(force=True)
            emit_loop_event(
                "reasoning_completed",
                {
                    "segment": reasoning_segment,
                    "content": content,
                    "character_count": len("".join(reasoning_parts)),
                    "truncated": truncated,
                },
            )
            reasoning_segment_active = False
            last_reasoning_progress_length = 0
        structured_display = ""
        def current_citation_bundle_payload() -> dict | None:
            """把已完成段落对应的引用快照序列化，供流式事件随正文一起下发。"""
            if not agent_answer_blocks:
                return None
            citation_bundle = compose_agent_citations(
                join_answer_blocks(agent_answer_blocks),
                agent_answer_blocks,
                loop_control.citation_registry,
                fetch_pages=False,
                prefetched_pages=loop_control.citation_page_prefetcher.completed_pages(),
            )
            if citation_bundle.status == "unavailable":
                return None
            return citation_bundle.model_dump(mode="json")

        def emit_answer_preview() -> None:
            nonlocal structured_display, response_start_emitted
            if answer_parser is None or not progress_callback:
                return
            delta = answer_parser.take_text_delta()
            if not delta:
                return
            structured_display += delta
            if not response_start_emitted:
                evaluate_pending("respond", "工具结果已经足够，模型开始生成段落级结构化回复")
                progress_callback(
                    "response_start",
                    {"observation_count": len(observations), "tool_count": len(observations)},
                )
                response_start_emitted = True
            payload: dict[str, Any] = {"delta": delta, "text": structured_display}
            citation_bundle_payload = current_citation_bundle_payload()
            if citation_bundle_payload is not None:
                payload["citation_bundle"] = citation_bundle_payload
            progress_callback("response_delta", payload)
        def emit_structured_block(block: AgentAnswerBlock) -> None:
            nonlocal response_start_emitted, structured_display
            valid_block = block.model_copy(
                update={"source_ids": loop_control.citation_registry.valid_source_ids(block.source_ids)}
            )
            agent_answer_blocks.append(valid_block)
            if not progress_callback:
                return
            if not response_start_emitted:
                evaluate_pending("respond", "工具结果已经足够，模型开始生成段落级结构化回复")
                progress_callback(
                    "response_start",
                    {"observation_count": len(observations), "tool_count": len(observations)},
                )
                response_start_emitted = True
            current_text = join_answer_blocks(agent_answer_blocks)
            progress_callback(
                "response_block",
                {
                    "delta": "",
                    "text": current_text,
                    "block": valid_block.model_dump(mode="json"),
                    "citation_bundle": current_citation_bundle_payload(),
                },
            )
            structured_display = current_text
        for event in events:
            if cancellation_check and cancellation_check():
                logger.info("agent loop cooperative cancellation checkpoint: run_id=%s", run_id)
                return replace(
                    self._cancelled_result(run_id=run_id, observations=observations, loop_events=loop_events),
                    usage=dict(effective_usage),
                )
            if loop_control.timed_out:
                evaluate_pending("stop", "达到总执行时长上限，停止继续调用")
                return replace(
                    self._build_result(
                        status=MAX_ITERATIONS,
                        response_text=f"任务执行已超过 {self.max_duration_seconds:g} 秒，为避免失控循环已经停止。",
                        observations=observations,
                        stop_reason="超过 Agent 循环总执行时长上限",
                        run_id=run_id,
                        execution_source=EXECUTION_SOURCE_AGENT_LOOP,
                        error_stage="loop_limit",
                        loop_events=loop_events,
                    ),
                    usage=dict(effective_usage),
                )
            event_type = _normalize_event_type(getattr(event, "event", ""))
            if not event_type and _is_run_output_object(event):
                # yield_run_output=True 时，Agno 在事件流末尾再 yield 一次 RunOutput：
                # 暂停态用于保存待确认现场，终态用于读取聚合后的 metrics。
                if _is_paused_run_output(event):
                    paused_run_output = event
                else:
                    final_run_output = event
                continue
            if event_type == "tool_call_started":
                finish_reasoning()
                if not agent_answer_blocks:
                    # 工具调用前的模型过渡语不属于最终回答，清空缓冲避免与终态 JSON 拼接。
                    content_parts.clear()
                    if structured_answer:
                        answer_parser = AgentAnswerStreamParser()
                evaluate_pending("continue", "上一轮结果不足，模型决定继续调用工具")
                tool = getattr(event, "tool", None)
                tool_name = _tool_name(tool)
                tool_args = _tool_args(tool)
                logger.info("tool started: tool=%s", tool_name)
                pending_starts[_tool_key(tool)] = time.perf_counter()
                iteration = next_iteration
                next_iteration += 1
                iteration_by_tool_key[_tool_key(tool)] = iteration
                emit_loop_event(
                    "iteration_started",
                    {
                        "iteration": iteration,
                        "tool": tool_name,
                        "name": _friendly_tool_name(tool_name),
                        "inputs": tool_args,
                    },
                )
                if progress_callback:
                    progress_callback(
                        "tool_start",
                        {
                            "index": iteration,
                            "name": _friendly_tool_name(tool_name),
                            "tool": tool_name,
                            "inputs": tool_args,
                        },
                    )
                continue
            if event_type in {"tool_call_completed", "tool_call_error"}:
                tool = getattr(event, "tool", None)
                started_at = pending_starts.pop(_tool_key(tool), None)
                duration_ms = int((time.perf_counter() - started_at) * 1000) if started_at else 0
                iteration = iteration_by_tool_key.pop(_tool_key(tool), len(observations) + 1)
                blocked_reason = loop_control.consume_block_reason(_tool_name(tool), _tool_args(tool))
                tool_signature = _tool_event_signature(tool)
                if not blocked_reason and tool_signature in completed_tool_signatures:
                    blocked_reason = "检测到相同工具和参数的重复调用，已在执行前拦截"
                if blocked_reason:
                    logger.warning("tool blocked by loop guard: tool=%s reason=%s", _tool_name(tool), blocked_reason)
                    if progress_callback:
                        progress_callback(
                            "tool_blocked",
                            {
                                "index": iteration,
                                "name": _friendly_tool_name(_tool_name(tool)),
                                "tool": _tool_name(tool),
                                "inputs": _tool_args(tool),
                                "reason": blocked_reason,
                            },
                        )
                    emit_loop_event(
                        "iteration_observed",
                        {
                            "iteration": iteration,
                            "status": "blocked",
                            "tool": _tool_name(tool),
                            "result": blocked_reason,
                            "duration_ms": duration_ms,
                        },
                    )
                    pending_evaluation = iteration
                    continue
                completed_tool_signatures.add(tool_signature)
                observation = _observation_from_tool(
                    index=iteration,
                    tool=tool,
                    duration_ms=duration_ms,
                )
                observations.append(observation)
                if observation.status == ERROR:
                    logger.warning(
                        "tool failed: tool=%s duration_ms=%s error=%s",
                        observation.tool,
                        observation.duration_ms,
                        observation.error_message,
                    )
                else:
                    logger.info("tool finished: tool=%s duration_ms=%s", observation.tool, observation.duration_ms)
                if progress_callback:
                    progress_callback("tool_end", {"observation": observation})
                guard_reason = loop_guard_reason(observation.output)
                emit_loop_event(
                    "iteration_observed",
                    {
                        "iteration": iteration,
                        "status": "blocked" if guard_reason else observation.status,
                        "tool": observation.tool,
                        "result": guard_reason or _compact_loop_text(observation.output or observation.error_message or "", 240),
                        "duration_ms": observation.duration_ms,
                    },
                )
                pending_evaluation = iteration
                continue
            if event_type == "run_content":
                reasoning_chunk = getattr(event, "reasoning_content", None)
                if isinstance(reasoning_chunk, str) and reasoning_chunk:
                    start_reasoning()
                    reasoning_parts.append(reasoning_chunk)
                    reasoning_segment_parts.append(reasoning_chunk)
                    update_reasoning_progress()
                chunk = getattr(event, "content", None)
                if isinstance(chunk, str) and chunk:
                    if chunk.strip() or content_parts:
                        finish_reasoning()
                    content_parts.append(chunk)
                    if answer_parser is not None:
                        completed_blocks = answer_parser.feed(chunk)
                        emit_answer_preview()
                        for block in completed_blocks:
                            emit_structured_block(block)
                    elif progress_callback:
                        if not response_start_emitted:
                            evaluate_pending("respond", "工具结果已经足够，模型开始生成最终回复")
                            progress_callback(
                                "response_start",
                                {"observation_count": len(observations), "tool_count": len(observations)},
                            )
                            response_start_emitted = True
                        progress_callback("response_delta", {"delta": chunk})
                continue
            if event_type == "reasoning_started":
                start_reasoning()
                continue
            if event_type == "reasoning_content_delta":
                start_reasoning()
                chunk = getattr(event, "reasoning_content", None) or getattr(event, "content", None)
                if isinstance(chunk, str) and chunk:
                    reasoning_parts.append(chunk)
                    reasoning_segment_parts.append(chunk)
                    update_reasoning_progress()
                continue
            if event_type == "reasoning_completed":
                content = getattr(event, "reasoning_content", None) or getattr(event, "content", None)
                if isinstance(content, str) and content:
                    if not reasoning_segment_parts:
                        reasoning_parts.append(content)
                        reasoning_segment_parts.append(content)
                    start_reasoning()
                finish_reasoning()
                continue
            if event_type == "run_completed":
                finish_reasoning()
                evaluate_pending("respond", "工具结果已经足够，模型结束循环并生成回复")
                final_content = getattr(event, "content", None)
                continue
            if event_type == "run_paused":
                evaluate_pending("stop", "下一步需要用户确认，循环暂时停止")
                confirmation_note = _confirmation_note(event)
                continue
            if event_type == "run_error":
                evaluate_pending("stop", "模型执行出错，循环已经停止")
                error_message = _coerce_text(getattr(event, "content", None)) or "模型执行出错。"
                continue
            if event_type == "run_cancelled":
                logger.info("agent loop received Agno cancellation event: run_id=%s", run_id)
                evaluate_pending("stop", "运行收到取消信号")
                return replace(
                    self._cancelled_result(run_id=run_id, observations=observations, loop_events=loop_events),
                    usage=dict(effective_usage),
                )
        if pending_evaluation is not None:
            decision = "stop" if error_message or confirmation_note else "respond"
            reason = "循环已经停止" if decision == "stop" else "工具调用结束，进入最终回复阶段"
            evaluate_pending(decision, reason)
        # 只采集真正执行过模型请求的终态 RunOutput；本地/skill/错误路径没有 RunOutput，
        # usage 保持空字典，避免用估算或 0 伪装成供应商上报数据。
        _accumulate_usage_from_run_output(effective_usage, final_run_output)
        if not response_start_emitted and progress_callback:
            progress_callback(
                "response_start",
                {"observation_count": len(observations), "tool_count": len(observations)},
            )
            response_start_emitted = True
        if answer_parser is not None:
            response_text = sanitize_response_text(finish_answer_stream(
                answer_parser, final_content or getattr(final_run_output, "content", None),
                agent_answer_blocks, emit_structured_block,
            ))
            if not response_text and not error_message:
                error_message = "模型返回了无法识别的回答格式，本次未能生成有效正文，请重试。"
            elif not answer_parser.complete and not error_message and not confirmation_note:
                # 半截 JSON 可以展示已收到的正文，但不能把不完整回答标记为任务完成。
                error_message = "模型回答格式未完整结束，已保留收到的部分正文。"
        else:
            response_text = sanitize_response_text(_coerce_text(final_content) or "".join(content_parts))
        if official_search_verification_failed(
            query,
            [item.output or "" for item in observations],
        ):
            response_text = official_search_failure_response(query)
        failed_observation = next((item for item in reversed(observations) if item.status == ERROR), None)
        if confirmation_note and failed_observation:
            failure_text = failed_observation.error_message or failed_observation.output or "工具执行失败。"
            logger.warning("agent loop tool failed before confirmation pause: tool=%s", failed_observation.tool)
            return replace(
                self._build_result(
                    status=ERROR,
                    response_text=failure_text,
                    observations=observations,
                    stop_reason="工具执行失败",
                    run_id=run_id,
                    execution_source=EXECUTION_SOURCE_AGENT_LOOP,
                    error_stage="tool_execution",
                    loop_events=loop_events,
                ),
                usage=dict(effective_usage),
            )
        if confirmation_note:
            if paused_run_output is not None:
                self.pause_store.save(
                    user_id=user_id,
                    session_id=session_id,
                    run_output=paused_run_output,
                    run_id=run_id,
                )
            logger.info("agent loop paused for confirmation")
            return self._build_result(
                status=NEEDS_CONFIRMATION,
                response_text=confirmation_note,
                observations=observations,
                stop_reason="等待用户确认执行高风险操作",
                run_id=run_id,
                execution_source=EXECUTION_SOURCE_AGENT_LOOP,
                error_stage="confirmation",
                loop_events=loop_events,
            )
        if error_message:
            # 已经输出部分正文也必须以失败结束，不能把截断的回答标成成功。
            if response_text:
                error_message = f"{response_text}\n\n回答未完成：{error_message}"
            logger.error("agent loop model execution error: %s", error_message)
            return replace(
                self._build_result(
                    status=ERROR,
                    response_text=error_message,
                    observations=observations,
                    stop_reason="模型输出中断" if response_text else "模型执行出错",
                    run_id=run_id,
                    execution_source=EXECUTION_SOURCE_AGENT_LOOP,
                    error_stage="model_runtime",
                    loop_events=loop_events,
                ),
                usage=dict(effective_usage),
            )
        if not response_text:
            response_text = "这次模型没有返回有效内容。你可以重试，或打开开发者详情查看本轮 Prompt 和执行轨迹。"
        failed = [item for item in observations if item.status == ERROR]
        status = SUCCESS
        stop_reason = "任务完成"
        handled_expected_failure = bool(failed) and (
            query_accepts_unavailable_result(query, response_text)
            or (any(item.status == SUCCESS for item in observations)
                and _response_acknowledges_tool_failure(response_text))
        )
        terminal_failure = bool(observations and observations[-1].status == ERROR)
        if terminal_failure and not handled_expected_failure:
            status = ERROR
            stop_reason = "最后一次工具调用失败"
            if not _response_acknowledges_tool_failure(response_text):
                failure_text = observations[-1].error_message or "工具执行失败。"
                response_text = f"最后一次工具调用失败，当前无法确认查询结果。\n\n{failure_text}"
        elif handled_expected_failure:
            stop_reason = "部分工具未成功，已依据已有结果说明限制"
        citation_bundle: CitationBundle | None = None
        if status == SUCCESS and loop_control.citation_registry.has_sources and agent_answer_blocks:
            citation_bundle = compose_agent_citations(
                response_text,
                agent_answer_blocks,
                loop_control.citation_registry,
                fetch_pages=False,
                prefetched_pages=loop_control.citation_page_prefetcher.completed_pages(),
            )
        logger.info(
            "agent loop finished: status=%s observations=%s %s",
            status,
            len(observations),
            format_cache_usage(effective_usage),
        )
        result = self._build_result(
            status=status,
            response_text=response_text,
            observations=observations,
            stop_reason=stop_reason,
            run_id=run_id,
            execution_source=EXECUTION_SOURCE_AGENT_LOOP,
            error_stage="tool_execution" if status == ERROR else None,
            loop_events=loop_events,
            citation_bundle=citation_bundle,
        )
        return replace(result, usage=dict(effective_usage))
    def _build_result(
        self,
        *,
        status: str,
        response_text: str,
        observations: list[StepObservation],
        stop_reason: str,
        run_id: str,
        execution_source: str,
        error_stage: str | None = None,
        loop_events: list[dict[str, Any]] | None = None,
        citation_bundle: CitationBundle | None = None,
    ) -> ExecutionResult:
        plan = _synthesize_plan(observations)
        return ExecutionResult(
            status=status,
            response_text=response_text,
            plan=plan,
            observations=observations,
            stop_reason=stop_reason,
            missing_info=[],
            run_id=run_id,
            execution_source=execution_source,
            error_stage=error_stage,
            loop_events=list(loop_events or []),
            citation_bundle=citation_bundle,
        )
    def _error_result(
        self,
        *,
        message: str,
        stop_reason: str,
        run_id: str,
        error_stage: str,
    ) -> ExecutionResult:
        return self._build_result(
            status=ERROR,
            response_text=message or stop_reason,
            observations=[],
            stop_reason=stop_reason,
            run_id=run_id,
            execution_source=EXECUTION_SOURCE_ERROR,
            error_stage=error_stage,
        )
    def _cancelled_result(
        self,
        *,
        run_id: str,
        observations: list[StepObservation] | None = None,
        loop_events: list[dict[str, Any]] | None = None,
    ) -> ExecutionResult:
        return self._build_result(
            status="cancelled",
            response_text="任务已取消。",
            observations=list(observations or []),
            stop_reason="用户取消任务",
            run_id=run_id,
            execution_source=EXECUTION_SOURCE_AGENT_LOOP,
            error_stage="cancellation",
            loop_events=loop_events,
        )

# 兼容旧导入名；主链路语义已经收敛为 AgentLoopRunner。
ReActRunner = AgentLoopRunner
