"""Agent 工具循环的执行守卫。"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from typing import Any, Callable

from daily_agent.search_policy import apply_search_argument_policy, filter_search_result_for_policy
from daily_agent.evidence import ToolResultCompactor, DOCUMENT_INLINE_TOKEN_LIMIT
from daily_agent.working_state import WorkingStateTracker, render_working_state_instruction
from daily_agent.citations.page_prefetch import CitationPagePrefetcher
from daily_agent.citations.registry import WebCitationRegistry
from daily_agent.tool_progress import business_arguments


REPEATED_CALL_BLOCKED = "LOOP_GUARD_REPEATED_CALL_BLOCKED"
TOOL_LIMIT_BLOCKED = "LOOP_GUARD_TOOL_LIMIT_BLOCKED"
TIME_LIMIT_BLOCKED = "LOOP_GUARD_TIME_LIMIT_BLOCKED"


class AgentLoopControl:
    """在工具真正运行前阻止失控循环，并记录可核验的调用次数。"""

    def __init__(
        self,
        max_tool_calls: int = 8,
        max_duration_seconds: float = 120,
        user_query: str = "",
        run_id: str = "",
        user_id: str = "default",
        session_id: str = "default",
        result_compactor: ToolResultCompactor | None = None,
        working_state_tracker: WorkingStateTracker | None = None,
        document_inline_token_limit: int = DOCUMENT_INLINE_TOKEN_LIMIT,
    ) -> None:
        self.max_tool_calls = max(1, int(max_tool_calls))
        # 被拦截的无效调用本来不占 max_tool_calls 额度，但需要一个独立上限兜底，
        # 否则重复调用同一个失败 URL 会让循环永不结束。
        self.max_blocked_calls = max(self.max_tool_calls * 2, 4)
        self.max_duration_seconds = max(0.0, float(max_duration_seconds))
        self.user_query = str(user_query or "")
        self.run_id = str(run_id or "")
        self.user_id = str(user_id or "default")
        self.session_id = str(session_id or "default")
        self.result_compactor = result_compactor or ToolResultCompactor(
            document_inline_token_limit=document_inline_token_limit
        )
        self.working_state_tracker = working_state_tracker
        self.citation_registry = WebCitationRegistry(self.run_id)
        self.citation_page_prefetcher = CitationPagePrefetcher()
        self.started_at = time.monotonic()
        self._call_count = 0
        self._blocked_count = 0
        self._tool_counts: Counter[str] = Counter()
        self._signatures: Counter[str] = Counter()
        self._blocked_reasons: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    @property
    def tool_call_count(self) -> int:
        """真正执行过的工具次数；被守卫拦截的重复调用不计入。"""
        with self._lock:
            return self._call_count

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def timed_out(self) -> bool:
        return self.max_duration_seconds > 0 and self.elapsed_seconds >= self.max_duration_seconds

    def tool_hook(
        self,
        function_name: str,
        function: Callable[..., Any],
        arguments: dict[str, Any],
    ) -> Any:
        """Agno 会把 ``function`` 作为下一层真实工具传入这里。"""
        arguments = apply_search_argument_policy(self.user_query, function_name, business_arguments(arguments))
        arguments = _apply_weather_argument_policy(self.user_query, function_name, arguments)
        signature = _call_signature(function_name, arguments)
        with self._lock:
            if self.timed_out:
                reason = (
                    f"{TIME_LIMIT_BLOCKED}: 已超过 {self.max_duration_seconds:g} 秒总耗时上限，"
                    "请停止继续调用工具并直接说明当前结果。"
                )
                self._remember_block(signature, reason)
                return reason
            if self._call_count >= self.max_tool_calls:
                reason = (
                    f"{TOOL_LIMIT_BLOCKED}: 已达到 {self.max_tool_calls} 次工具调用上限，"
                    "请停止继续调用工具并基于已有结果回答。"
                )
                self._remember_block(signature, reason)
                return reason
            # 被守卫拦截的调用没有真正执行，不占用 max_tool_calls 额度；
            # 否则模型重复调用失败 URL 会先把预算耗光，反而更早失去排查机会。
            if self._blocked_count >= self.max_blocked_calls:
                reason = (
                    f"{TOOL_LIMIT_BLOCKED}: 已被拦截的无效调用达到 {self.max_blocked_calls} 次，"
                    "请停止继续调用工具并基于已有结果回答。"
                )
                self._remember_block(signature, reason)
                return reason
            if _is_extraterrestrial_weather_query(self.user_query, function_name) and self._tool_counts[function_name] >= 1:
                reason = (
                    f"{REPEATED_CALL_BLOCKED}: 地外地点不属于城市天气服务范围，"
                    "禁止缩短地名后再次尝试，以免误命中地球上的同名地点。"
                )
                self._blocked_count += 1
                self._remember_block(signature, reason)
                return reason
            if self._signatures[signature] >= 1:
                reason = (
                    f"{REPEATED_CALL_BLOCKED}: 相同工具和参数已经执行过，禁止重复执行。"
                    "请更换参数、选择其他工具，或基于已有结果回答。"
                )
                self._blocked_count += 1
                self._remember_block(signature, reason)
                return reason
            self._call_count += 1
            self._tool_counts[function_name] += 1
            self._signatures[signature] += 1
        result = function(**arguments)
        result = filter_search_result_for_policy(self.user_query, function_name, result)
        # 引用注册必须发生在结果压缩之前。后续 Agno Tool 事件拿到的是面向模型的
        # 紧凑文本，原始搜索页面、URL 和摘要可能已经被截断，届时无法再可靠溯源。
        captured_source_ids = self.citation_registry.capture(function_name, result)
        self.citation_page_prefetcher.schedule(self.citation_registry, captured_source_ids)
        if not self.run_id:
            return result
        # Tool 本体允许并发；结果压缩和 WorkingState 累加需要串行，避免多个
        # 工具同时完成时丢失 token 计数或生成相互覆盖的状态快照。
        with self._lock:
            compacted = self.result_compactor.compact(
                result,
                run_id=self.run_id,
                user_id=self.user_id,
                session_id=self.session_id,
                tool_name=function_name,
            )
            if self.working_state_tracker is not None:
                snapshot = self.working_state_tracker.add_token_usage(
                    original=compacted.original_estimated_tokens,
                    injected=compacted.injected_estimated_tokens,
                )
                if compacted.evidence_id:
                    model_content = (
                        f"{compacted.model_content}\n\n"
                        f"{render_working_state_instruction(snapshot)}"
                    )
                else:
                    model_content = compacted.model_content
            else:
                model_content = compacted.model_content
        citation_context = self.citation_registry.render_model_context(captured_source_ids)
        if citation_context:
            return f"{model_content}\n\n{citation_context}"
        return model_content

    def consume_block_reason(self, function_name: str, arguments: dict[str, Any]) -> str | None:
        signature = _call_signature(function_name, arguments)
        with self._lock:
            reasons = self._blocked_reasons.get(signature)
            if not reasons:
                return None
            reason = reasons.pop(0)
            if not reasons:
                self._blocked_reasons.pop(signature, None)
        return loop_guard_reason(reason) or reason

    def _remember_block(self, signature: str, reason: str) -> None:
        self._blocked_reasons.setdefault(signature, []).append(reason)


def loop_guard_reason(value: str | None) -> str | None:
    """把内部守卫标记转成人能看懂、可安全展示的终止原因。"""
    text = str(value or "")
    if REPEATED_CALL_BLOCKED in text:
        return "检测到相同工具和参数的重复调用，已在执行前拦截"
    if TOOL_LIMIT_BLOCKED in text:
        return "达到工具调用次数上限，已停止继续调用"
    if TIME_LIMIT_BLOCKED in text:
        return "达到总执行时长上限，已停止继续调用"
    return None


def _call_signature(function_name: str, arguments: dict[str, Any]) -> str:
    """参数先稳定排序，确保键顺序不同但内容相同的调用也会被识别。"""
    try:
        normalized = json.dumps(business_arguments(arguments), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        normalized = repr(arguments)
    return f"{function_name}:{normalized}"


def _apply_weather_argument_policy(user_query: str, function_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """修正常见跨国城市的歧义，避免地理编码器优先匹配中国同名地点。"""
    if "weather" not in function_name.lower():
        return arguments
    query = str(user_query or "").lower()
    city = str(arguments.get("city") or "").strip().lower()
    if city in {"东京", "tokyo"} and "江苏" not in query:
        arguments["city"] = "Tokyo, Japan"
    return arguments


def _is_extraterrestrial_weather_query(user_query: str, function_name: str) -> bool:
    if "weather" not in function_name.lower():
        return False
    query = str(user_query or "").lower()
    return any(token in query for token in ("火星", "mars", "月球", "moon", "木星", "jupiter"))
