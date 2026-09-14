"""把 MySQL 里的多类记忆组装成 Agent 可读的「用户上下文」。

设计目标
--------
让模型在单次 ask 里尽量「记得住用户」，同时控制注入 token/字符量。

数据分层（优先级从高到低，预算不足时从后往前截断）
--------------------------------------------------
1. 用户偏好 — 结构化键值，如 language=zh
2. 长期记忆 — 自然语言句子
3. 会话摘要 — 更早轮次经规则压缩后的结论（见 conversation_summary）
4. 最近会话 — 最近 N 轮 user/assistant 原文（滑动窗口）
5. 最近任务与工具结果不跨会话注入，避免旧回答和旧数据污染当前对话

会话「摘要 + 原文」策略
----------------------
- 最近 conversation_turn_limit 轮（默认 3 轮 = 6 条消息）保留原文；
- 更早的消息：优先读 conversation_session_summaries 表；
  若尚未持久化摘要，则对窗口外的消息现场调用 compress_conversation_history。
- 每次 ask 结束后 cli 会调用 refresh_conversation_summary 把摘要写回库。

与 TaskExecutor / Planner 的关系
-------------------------------
- Planner 决定「要做什么」；本模块决定「用户之前说过什么」。
- Executor 在总结阶段也会 build_agent，同样会走 build_instructions 注入上下文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import re

from daily_agent.config import DEFAULT_CONTEXT_TOKEN_BUDGET
from daily_agent.conversation_summary import compact_text, compress_conversation_history, format_conversation_message
from daily_agent.memory import (
    DEFAULT_CONVERSATION_TURN_LIMIT,
    DEFAULT_SESSION_ID,
    DEFAULT_USER_ID,
    MySQLMemoryStore,
)
from daily_agent.memory_retrieval import select_relevant_memories
from daily_agent.request_preparation import reuse_preparation
from daily_agent.response_constraints import extract_response_constraints
from daily_agent.skill_catalog import list_enabled_tool_names

_TOKEN_ENCODER = None
_TOKEN_ENCODER_LOADED = False
_REMOVED_DOMAIN_CONTEXT_RE = re.compile(
    r"btc|bitcoin|比特币|crypto|coingecko|amadeus|flight|flights|航班|机票|交易所|合约策略",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AgentContext:
    """一次 ask 前从库里聚合出的全部上下文片段（尚未做字符预算裁剪）。"""

    user_id: str
    preferences: list[str]
    memories: list[str]
    conversation_summary: list[str]
    recent_conversation: list[str]
    recent_tasks: list[str]
    recent_tool_calls: list[str]
    active_constraints: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.preferences
            or self.memories
            or self.conversation_summary
            or self.recent_conversation
            or self.recent_tasks
            or self.recent_tool_calls
            or self.active_constraints
        )

    def to_instructions(
        self,
        token_budget: int | None = DEFAULT_CONTEXT_TOKEN_BUDGET,
        *,
        char_budget: int | None = None,
    ) -> str:
        """渲染为追加到 Agent instructions 的中文块。

        token_budget 为 None 时不截断；否则按 sections 顺序累加行，
        超出预算时在末尾附加截断标记（不删除数据库里的原始数据）。
        char_budget 仅用于兼容旧调用方；新代码请改用 token_budget。
        """
        if self.is_empty():
            return ""

        lines = [
            "用户上下文：",
            "使用以下上下文来减少重复提问；如果上下文不足，必须继续向用户确认，不要编造。",
        ]
        if self.active_constraints:
            lines.append("当前会话约束（必须遵守）：")
            lines.extend(f"- {item}" for item in self.active_constraints)
        if self.preferences:
            lines.append("用户偏好：")
            lines.extend(f"- {item}" for item in self.preferences)
        if self.memories:
            lines.append("长期记忆：")
            lines.extend(f"- {item}" for item in self.memories)
        if self.conversation_summary:
            lines.append("会话摘要：")
            lines.extend(f"- {item}" for item in self.conversation_summary)
        if self.recent_conversation:
            lines.append("最近会话：")
            lines.extend(f"- {item}" for item in self.recent_conversation)
        if self.recent_tasks:
            lines.append("最近任务：")
            lines.extend(f"- {item}" for item in self.recent_tasks)
        if self.recent_tool_calls:
            lines.append("最近工具结果：")
            lines.extend(f"- {item}" for item in self.recent_tool_calls)
        if char_budget is not None:
            return _render_lines_with_char_budget(lines, char_budget)
        return _render_lines_with_token_budget(lines, token_budget)

    def describe_budget(
        self,
        token_budget: int | None = DEFAULT_CONTEXT_TOKEN_BUDGET,
        *,
        char_budget: int | None = None,
    ) -> dict[str, object]:
        lines = _build_context_lines(self)
        sections = _build_context_sections(self)
        section_usage = _estimate_section_usage(
            context=self,
            lines=lines,
            token_budget=token_budget,
            char_budget=char_budget,
        )
        if char_budget is not None:
            rendered_text = _render_lines_with_char_budget(lines, char_budget)
            limit_kind = "char"
            limit_value = char_budget
            used_value = len(rendered_text)
            total_value = len("\n".join(lines))
        else:
            rendered_text = _render_lines_with_token_budget(lines, token_budget)
            limit_kind = "token"
            limit_value = token_budget
            used_value = estimate_text_tokens(rendered_text)
            total_value = estimate_text_tokens("\n".join(lines))
        rendered_sections = _detect_rendered_sections(rendered_text, sections)
        cutoff_marker = "…（上下文已截断）"
        return {
            "limit_kind": limit_kind,
            "limit_value": limit_value,
            "used_value": used_value,
            "total_value": total_value,
            "truncated": cutoff_marker in rendered_text,
            "rendered_sections": rendered_sections,
            "dropped_sections": [name for name in sections if name not in rendered_sections],
            "section_usage": section_usage,
            "rendered_text": rendered_text,
        }


@dataclass(frozen=True)
class ContextObservation:
    instructions: str
    budget: dict[str, object]
    compression: dict[str, object]


@dataclass(frozen=True)
class PromptMessageContext:
    """发送给 Agno 的会话背景与原生 role 历史。

    session_context 放在历史之前，只装几乎不变的偏好，避免它一变就连累整段历史前缀；
    volatile_context 装长期记忆和冻结摘要，随记忆写入变化，必须排在历史之后。
    """

    session_context: str
    history_messages: list[dict[str, str]]
    cache_epoch: str
    summarized_message_count: int
    volatile_context: str = ""


class MySQLContextBuilder:
    """从 MySQLMemoryStore 读取并组装 AgentContext。"""

    def __init__(self, store: MySQLMemoryStore | None = None) -> None:
        self.store = store or MySQLMemoryStore()

    def build(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        query: str | None = None,
        memory_limit: int = 5,
        conversation_turn_limit: int = DEFAULT_CONVERSATION_TURN_LIMIT,
        task_limit: int = 5,
        tool_limit: int = 5,
    ) -> AgentContext:
        """聚合六类上下文。

        conversation_turn_limit: 保留最近几「轮」对话（1 轮 = 1 条 user + 1 条 assistant）。
        list_conversation_messages 按时间倒序返回，这里 reverse 成时间正序便于截取窗口。
        """
        preferences = _format_preferences(self.store.list_preferences(user_id=user_id))
        memories = _filter_removed_domain_context(
            self._relevant_memories(user_id=user_id, query=query, memory_limit=memory_limit)
        )

        recent_window = max(0, int(conversation_turn_limit)) * 2  # 每轮两条消息
        stored_summary = self.store.get_conversation_summary(user_id=user_id, session_id=session_id)
        if stored_summary and stored_summary.summary_text:
            conversation_summary = _filter_removed_domain_context(stored_summary.summary_text.splitlines())
            recent_items = list(
                reversed(
                    self.store.list_conversation_messages(
                        user_id=user_id,
                        session_id=session_id,
                        limit=recent_window,
                    )
                )
            ) if recent_window else []
        else:
            all_conversation = list(
                reversed(self.store.list_conversation_messages(user_id=user_id, session_id=session_id))
            )
            old_conversation = all_conversation[:-recent_window] if recent_window else all_conversation
            conversation_summary = _filter_removed_domain_context(compress_conversation_history(old_conversation))
            recent_items = all_conversation[-recent_window:] if recent_window else []

        recent_conversation = _filter_removed_domain_context(
            [format_conversation_message(item) for item in recent_items]
        )
        active_constraints = extract_response_constraints(recent_items, current_query=query or "")
        # task_history 和 tool_call_logs 目前是用户级记录，没有可靠的 session_id 查询边界。
        # 当前会话已经由摘要和最近原文覆盖，继续注入全局输出会把其他会话的错误答案带进来。
        recent_tasks: list[str] = []
        recent_tool_calls: list[str] = []
        return AgentContext(
            user_id=user_id,
            preferences=preferences,
            memories=memories,
            conversation_summary=conversation_summary,
            recent_conversation=recent_conversation,
            recent_tasks=recent_tasks,
            recent_tool_calls=recent_tool_calls,
            active_constraints=active_constraints,
        )

    def _relevant_memories(self, *, user_id: str, query: str | None, memory_limit: int) -> list[str]:
        if not query or not hasattr(self.store, "list_memories"):
            return self.store.recent_memories(user_id=user_id, limit=memory_limit)
        candidates = self.store.list_memories(user_id=user_id, limit=max(50, memory_limit * 20))
        return select_relevant_memories(query, candidates, limit=memory_limit)

    def build_instructions(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        token_budget: int | None = DEFAULT_CONTEXT_TOKEN_BUDGET,
        *,
        query: str | None = None,
        char_budget: int | None = None,
    ) -> str:
        """build + to_instructions 的便捷入口，供 agent.build_agent 调用。"""
        return self.build(user_id=user_id, session_id=session_id, query=query).to_instructions(
            token_budget=token_budget,
            char_budget=char_budget,
        )

    def build_fast_instructions(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        *,
        query: str | None = None,
        conversation_turn_limit: int = 2,
    ) -> str:
        """为快答读取最小上下文，不扫描或现场压缩完整会话历史。

        快答只需要延续称呼、偏好和最近两轮对话。这里按 LIMIT 直接读取最近消息，
        并使用字符预算渲染，避免为了短对话首次加载大型分词器。长期记忆检索、历史摘要
        和全量会话压缩保留给完整 Agent，防止一条普通问答被历史数据拖慢。
        """
        recent_limit = max(0, int(conversation_turn_limit)) * 2
        recent_items = list(
            reversed(
                self.store.list_conversation_messages(
                    user_id=user_id,
                    session_id=session_id,
                    limit=recent_limit,
                )
            )
        ) if recent_limit else []
        context = AgentContext(
            user_id=user_id,
            preferences=_format_preferences(self.store.list_preferences(user_id=user_id)),
            memories=[],
            conversation_summary=[],
            recent_conversation=_filter_removed_domain_context(
                [format_conversation_message(item) for item in recent_items]
            ),
            recent_tasks=[],
            recent_tool_calls=[],
            active_constraints=extract_response_constraints(recent_items, current_query=query or ""),
        )
        return context.to_instructions(char_budget=12_000)

    @reuse_preparation
    def build_prompt_message_context(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        token_budget: int | None = DEFAULT_CONTEXT_TOKEN_BUDGET,
        *,
        current_run_id: str | None = None,
        fast_mode: bool = False,
    ) -> PromptMessageContext:
        """构建稳定的会话 epoch 头和原生 user/assistant 历史。

        完整 Agent 使用已持久化摘要作为 epoch 高水位，只发送摘要之后的原始消息。
        摘要在达到压缩阈值前保持不变，因此同一 epoch 内的请求表现为在上一轮消息后
        继续追加，DeepSeek 可以复用完整公共前缀。快答仍只取最近两轮，避免普通短问答
        因冷缓存加载过长历史；两条链路都会排除 API 在运行开始时预写的当前 user 消息，
        防止当前问题既出现在历史里又作为本轮 user 输入发送一次。

        易变内容分两段：偏好（很少变）留在历史之前；长期记忆和冻结摘要放在
        volatile_context，由调用方追加到历史之后。记忆写入因此只影响尾部，
        不会再让整段历史失去缓存。带 reuse_preparation 是为了同一问答作用域内
        多处调用（例如客户端回显去重）只读一次数据库。
        """
        stored_summary = self.store.get_conversation_summary(user_id=user_id, session_id=session_id)
        if fast_mode:
            raw_items = list(
                reversed(
                    self.store.list_conversation_messages(
                        user_id=user_id,
                        session_id=session_id,
                        limit=DEFAULT_CONVERSATION_TURN_LIMIT * 2,
                    )
                )
            )
            summarized_message_count = 0
            summary_lines: list[str] = []
        else:
            raw_items = list(
                reversed(self.store.list_conversation_messages(user_id=user_id, session_id=session_id))
            )
            summarized_message_count = min(
                max(0, int(stored_summary.summarized_message_count)) if stored_summary else 0,
                len(raw_items),
            )
            raw_items = raw_items[summarized_message_count:]
            summary_lines = (
                _filter_removed_domain_context(stored_summary.summary_text.splitlines())
                if stored_summary and stored_summary.summary_text
                else []
            )

        history_messages = _native_history_messages(raw_items, current_run_id=current_run_id)
        history_tokens = sum(
            estimate_text_tokens(item["content"]) + 4 for item in history_messages
        )
        preferences = _format_preferences(self.store.list_preferences(user_id=user_id))
        memories = [] if fast_mode else _filter_removed_domain_context(
            self.store.recent_memories(user_id=user_id, limit=5)
        )
        session_token_budget = (
            None if token_budget is None else max(0, int(token_budget) - history_tokens)
        )
        session_context = AgentContext(
            user_id=user_id,
            preferences=preferences,
            memories=[],
            conversation_summary=[],
            recent_conversation=[],
            recent_tasks=[],
            recent_tool_calls=[],
            active_constraints=[],
        ).to_instructions(
            token_budget=session_token_budget,
            char_budget=12_000 if fast_mode else None,
        )
        volatile_context = _render_volatile_context(
            user_id=user_id,
            memories=memories,
            summary_lines=summary_lines,
            remaining_token_budget=(
                None
                if session_token_budget is None
                else session_token_budget - estimate_text_tokens(session_context)
            ),
        )
        epoch_source = "\n".join(
            (
                str(summarized_message_count),
                session_context,
                volatile_context,
                "fast" if fast_mode else "full",
            )
        )
        cache_epoch = hashlib.sha256(epoch_source.encode("utf-8")).hexdigest()[:16]
        return PromptMessageContext(
            session_context=session_context,
            history_messages=history_messages,
            cache_epoch=cache_epoch,
            summarized_message_count=summarized_message_count,
            volatile_context=volatile_context,
        )

    def build_observation(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        token_budget: int | None = DEFAULT_CONTEXT_TOKEN_BUDGET,
        *,
        char_budget: int | None = None,
        memory_limit: int = 5,
        conversation_turn_limit: int = DEFAULT_CONVERSATION_TURN_LIMIT,
        task_limit: int = 5,
        tool_limit: int = 5,
    ) -> ContextObservation:
        del memory_limit, conversation_turn_limit, task_limit, tool_limit
        prompt_context = self.build_prompt_message_context(
            user_id=user_id,
            session_id=session_id,
            token_budget=token_budget,
        )
        rendered_parts = [prompt_context.session_context] if prompt_context.session_context else []
        rendered_parts.extend(
            f"{item['role']}: {item['content']}" for item in prompt_context.history_messages
        )
        if prompt_context.volatile_context:
            rendered_parts.append(prompt_context.volatile_context)
        rendered_text = "\n\n".join(rendered_parts)
        used_tokens = estimate_text_tokens(rendered_text)
        history_tokens = sum(
            estimate_text_tokens(item["content"]) + 4 for item in prompt_context.history_messages
        )
        volatile_tokens = (
            estimate_text_tokens(prompt_context.volatile_context)
            if prompt_context.volatile_context
            else 0
        )
        session_tokens = max(0, used_tokens - history_tokens - volatile_tokens)
        budget = {
            "limit_kind": "char" if char_budget is not None else "token",
            "limit_value": char_budget if char_budget is not None else token_budget,
            "used_value": len(rendered_text) if char_budget is not None else used_tokens,
            "total_value": len(rendered_text) if char_budget is not None else used_tokens,
            "truncated": (
                "…（上下文已截断）" in prompt_context.session_context
                or "…（上下文已截断）" in prompt_context.volatile_context
            ),
            "rendered_sections": [
                name
                for name, present in (
                    ("会话背景", bool(prompt_context.session_context)),
                    ("原生角色历史", bool(prompt_context.history_messages)),
                    ("长期记忆与摘要", bool(prompt_context.volatile_context)),
                )
                if present
            ],
            "dropped_sections": [],
            "section_usage": [
                {
                    "key": "session_context",
                    "label": "会话背景",
                    "estimated_tokens": session_tokens,
                    "injected": bool(prompt_context.session_context),
                },
                {
                    "key": "recent_conversation",
                    "label": "原生角色历史",
                    "estimated_tokens": history_tokens,
                    "injected": bool(prompt_context.history_messages),
                },
                {
                    "key": "volatile_context",
                    "label": "长期记忆与摘要",
                    "estimated_tokens": volatile_tokens,
                    "injected": bool(prompt_context.volatile_context),
                },
            ],
            "rendered_text": rendered_text,
        }
        stored_summary = self.store.get_conversation_summary(user_id=user_id, session_id=session_id)
        compression = {
            "summary_source": "stored_epoch" if stored_summary and stored_summary.summary_text else "none",
            "summary_present": bool(stored_summary and stored_summary.summary_text),
            "summary_line_count": len(stored_summary.summary_text.splitlines()) if stored_summary else 0,
            "summarized_message_count": prompt_context.summarized_message_count,
            "recent_window_messages": len(prompt_context.history_messages),
            "stored_summary_updated_at": getattr(stored_summary, "updated_at", None),
            "summary_text_preview": "\n".join(stored_summary.summary_text.splitlines()[:2]) if stored_summary else "",
            "cache_epoch": prompt_context.cache_epoch,
        }
        return ContextObservation(
            instructions=str(budget["rendered_text"]),
            budget=budget,
            compression=compression,
        )


def _summarize_task(item) -> str:
    text = item.output_summary or item.error_message or item.user_input
    return f"{item.task_type} {item.status}: {compact_text(text, 180)}"


def _summarize_tool_call(item) -> str:
    text = item.output_summary or item.error_message or ""
    return f"{item.tool_name} {item.status} {item.duration_ms}ms: {compact_text(text, 180)}"


def _recent_task_summaries(items, *, limit: int) -> list[str]:
    """保留近期任务上下文，但跳过已下架领域造成的旧污染。"""
    summaries: list[str] = []
    for item in items:
        summary = _summarize_task(item)
        if _looks_like_removed_domain_context(summary):
            continue
        summaries.append(summary)
        if len(summaries) >= limit:
            break
    return summaries


def _recent_tool_call_summaries(items, *, enabled_tools: set[str], limit: int) -> list[str]:
    """只注入当前仍启用的工具结果，避免旧工具日志被模型当成现有能力。"""
    summaries: list[str] = []
    for item in items:
        tool_name = str(getattr(item, "tool_name", "") or "")
        if enabled_tools and tool_name not in enabled_tools:
            continue
        summary = _summarize_tool_call(item)
        if _looks_like_removed_domain_context(summary):
            continue
        summaries.append(summary)
        if len(summaries) >= limit:
            break
    return summaries


def _looks_like_removed_domain_context(text: str) -> bool:
    return bool(_REMOVED_DOMAIN_CONTEXT_RE.search(text or ""))


def _filter_removed_domain_context(items: list[str]) -> list[str]:
    """过滤已下架垂直能力留下的上下文，避免模型误认为当前仍支持。"""
    return [item for item in items if not _looks_like_removed_domain_context(item)]


def _native_history_messages(items, *, current_run_id: str | None) -> list[dict[str, str]]:
    """保留数据库中的真实角色与正文，并排除本轮提前持久化的用户消息。"""
    messages: list[dict[str, str]] = []
    for item in items:
        role = str(getattr(item, "role", "") or "").strip().lower()
        content = str(getattr(item, "content", "") or "").strip()
        run_id = str(getattr(item, "run_id", "") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        if current_run_id and run_id == current_run_id:
            continue
        if _looks_like_removed_domain_context(content):
            continue
        messages.append({"role": role, "content": content})
    return messages


def _render_volatile_context(
    *,
    user_id: str,
    memories: list[str],
    summary_lines: list[str],
    remaining_token_budget: int | None,
) -> str:
    """渲染放在历史之后的易变背景（长期记忆 + 冻结摘要）。

    这些内容会随记忆写入变化，放在历史之前会让整段历史一起失去缓存，
    放在历史之后只会影响尾部。内容为空或预算耗尽时返回空串，
    避免在尾部塞一个孤立的截断标记。
    """
    if not memories and not summary_lines:
        return ""
    if remaining_token_budget is not None and remaining_token_budget <= 0:
        return ""
    rendered = AgentContext(
        user_id=user_id,
        preferences=[],
        memories=memories,
        conversation_summary=summary_lines,
        recent_conversation=[],
        recent_tasks=[],
        recent_tool_calls=[],
        active_constraints=[],
    ).to_instructions(token_budget=remaining_token_budget)
    return "" if rendered == "…（上下文已截断）" else rendered


def _build_context_lines(context: AgentContext) -> list[str]:
    if context.is_empty():
        return []
    lines = [
        "用户上下文：",
        "使用以下上下文来减少重复提问；如果上下文不足，必须继续向用户确认，不要编造。",
    ]
    if context.active_constraints:
        lines.append("当前会话约束（必须遵守）：")
        lines.extend(f"- {item}" for item in context.active_constraints)
    if context.preferences:
        lines.append("用户偏好：")
        lines.extend(f"- {item}" for item in context.preferences)
    if context.memories:
        lines.append("长期记忆：")
        lines.extend(f"- {item}" for item in context.memories)
    if context.conversation_summary:
        lines.append("会话摘要：")
        lines.extend(f"- {item}" for item in context.conversation_summary)
    if context.recent_conversation:
        lines.append("最近会话：")
        lines.extend(f"- {item}" for item in context.recent_conversation)
    if context.recent_tasks:
        lines.append("最近任务：")
        lines.extend(f"- {item}" for item in context.recent_tasks)
    if context.recent_tool_calls:
        lines.append("最近工具结果：")
        lines.extend(f"- {item}" for item in context.recent_tool_calls)
    return lines


def _build_context_sections(context: AgentContext) -> list[str]:
    sections: list[str] = []
    if context.active_constraints:
        sections.append("当前会话约束（必须遵守）")
    if context.preferences:
        sections.append("用户偏好")
    if context.memories:
        sections.append("长期记忆")
    if context.conversation_summary:
        sections.append("会话摘要")
    if context.recent_conversation:
        sections.append("最近会话")
    if context.recent_tasks:
        sections.append("最近任务")
    if context.recent_tool_calls:
        sections.append("最近工具结果")
    return sections


def _detect_rendered_sections(rendered_text: str, sections: list[str]) -> list[str]:
    return [name for name in sections if f"{name}：" in rendered_text]


_SECTION_USAGE_META: tuple[tuple[str, str, str], ...] = (
    ("context_instruction", "上下文说明", "用户上下文："),
    ("active_constraints", "当前会话约束", "当前会话约束（必须遵守）："),
    ("preferences", "用户偏好", "用户偏好："),
    ("memories", "长期记忆", "长期记忆："),
    ("conversation_summary", "会话摘要", "会话摘要："),
    ("recent_conversation", "最近会话", "最近会话："),
    ("recent_tasks", "最近任务", "最近任务："),
    ("recent_tool_calls", "最近工具结果", "最近工具结果："),
)


def _estimate_section_usage(
    *,
    context: AgentContext,
    lines: list[str],
    token_budget: int | None,
    char_budget: int | None,
) -> list[dict[str, object]]:
    """按「本次实际注入的 rendered 文本」估算每个上下文分类的 token 数。

    这里的核心口径与 _render_lines_with_token_budget / _render_lines_with_char_budget
    完全一致（逐行累加、超预算即截断），保证各分类合计不超过 used_value；
    通用头部单独作为「上下文说明」项，截断标记这类小开销留在未分类里。
    估算只用于展示，不改变任何注入、截断或数据库行为。
    """
    sections = _build_context_sections(context)
    # 只有真实存在的分类才出现，避免把空分类展示成 0。
    expected_sections: set[str] = {"context_instruction"}
    for section in sections:
        for key, label, _header in _SECTION_USAGE_META:
            if key != "context_instruction" and label == section:
                expected_sections.add(key)
                break
    token_by_section: dict[str, int] = {}
    injected_by_section: dict[str, bool] = {}
    current_key = "context_instruction"
    used = 0
    rendered_any = False
    cutoff_marker = "…（上下文已截断）"
    for index, line in enumerate(lines):
        # 逐行判断分类归属：出现分类标题就切换，其余行属于当前分类。
        for key, _label, header in _SECTION_USAGE_META:
            if line == header:
                current_key = key
                break
        if char_budget is not None:
            next_cost = len(line) if index == 0 else len(line) + 1
            over_budget = used + next_cost > char_budget
        else:
            next_cost = estimate_text_tokens(line) if index == 0 else estimate_text_tokens("\n" + line)
            over_budget = token_budget is not None and used + next_cost > token_budget
        if over_budget:
            if not rendered_any and current_key in expected_sections:
                injected_by_section.setdefault(current_key, False)
            break
        rendered_any = True
        used += next_cost
        token_by_section[current_key] = token_by_section.get(current_key, 0) + next_cost
        injected_by_section[current_key] = True
        # 与渲染函数一致：截断标记只在预算裁切生效时追加，未渲染则不累加到分类；
        # 预算为 None（不截断）时同样不追加标记，避免估算与 used_value 口径分叉。
    section_usage: list[dict[str, object]] = []
    for key, label, _header in _SECTION_USAGE_META:
        if key not in expected_sections:
            continue
        section_usage.append(
            {
                "key": key,
                "label": label,
                "estimated_tokens": token_by_section.get(key, 0),
                "injected": bool(injected_by_section.get(key, False)),
            }
        )
    return section_usage


def _format_preferences(items) -> list[str]:
    labels = {
        "self_identity": "用户明确自述的身份",
    }
    return [f"{labels.get(item.key, item.key)}: {item.value}" for item in items]


def _render_lines_with_token_budget(lines: list[str], token_budget: int | None) -> str:
    """按行累加 token，超出 token_budget 时停止并标注截断。

    注意：这是注入 prompt 时的裁剪，不会修改 MySQL 中的原始记录。
    前面的 section（偏好、记忆、摘要）因此比后面的（任务、工具日志）更不易被裁掉。
    """
    if token_budget is None:
        return "\n".join(lines)
    if token_budget <= 0:
        return ""

    rendered: list[str] = []
    used = 0
    cutoff_marker = "…（上下文已截断）"
    for line in lines:
        next_cost = estimate_text_tokens(line) if not rendered else estimate_text_tokens("\n" + line)
        if used + next_cost > token_budget:
            if not rendered:
                return cutoff_marker
            if used + estimate_text_tokens("\n" + cutoff_marker) <= token_budget:
                rendered.append(cutoff_marker)
            break
        rendered.append(line)
        used += next_cost
    return "\n".join(rendered)


def _render_lines_with_char_budget(lines: list[str], char_budget: int | None) -> str:
    """兼容旧版 char_budget 的字符裁剪逻辑。"""
    if char_budget is None:
        return "\n".join(lines)
    if char_budget <= 0:
        return ""

    rendered: list[str] = []
    used = 0
    cutoff_marker = "…（上下文已截断）"
    for line in lines:
        next_cost = len(line) if not rendered else len(line) + 1
        if used + next_cost > char_budget:
            if not rendered:
                return cutoff_marker[:char_budget]
            if used + len(cutoff_marker) + 1 <= char_budget:
                rendered.append(cutoff_marker)
            break
        rendered.append(line)
        used += next_cost
    return "\n".join(rendered)


def estimate_text_tokens(text: str) -> int:
    encoder = _get_token_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass
    return _estimate_text_tokens_fallback(text)


def _get_token_encoder():
    global _TOKEN_ENCODER, _TOKEN_ENCODER_LOADED
    if _TOKEN_ENCODER_LOADED:
        return _TOKEN_ENCODER
    _TOKEN_ENCODER_LOADED = True
    try:
        import tiktoken
    except ImportError:
        _TOKEN_ENCODER = None
        return _TOKEN_ENCODER
    try:
        _TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _TOKEN_ENCODER = None
    return _TOKEN_ENCODER


def _estimate_text_tokens_fallback(text: str) -> int:
    compact = " ".join(text.split())
    if not compact:
        return 0

    chinese_tokens = len(re.findall(r"[\u3400-\u9fff]", compact))
    latin_tokens = sum(
        max(1, math.ceil(len(token) / 4))
        for token in re.findall(r"[A-Za-z0-9_./:-]+", compact)
    )
    punctuation_tokens = len(re.findall(r"[^\w\s\u3400-\u9fff]", compact))
    whitespace_tokens = compact.count(" ")
    estimated = chinese_tokens + latin_tokens + punctuation_tokens + whitespace_tokens
    return max(1, estimated)
