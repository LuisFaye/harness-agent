from __future__ import annotations

from typing import Any

MAX_CLIENT_CONTEXT_MESSAGES = 12
MAX_CLIENT_CONTEXT_CHARS = 6000
ALLOWED_CLIENT_CONTEXT_ROLES = {"user", "assistant", "system"}


def build_client_context_instructions(
    messages: list[Any] | None,
    *,
    current_query: str = "",
    history_messages: list[dict[str, str]] | None = None,
) -> str:
    """把前端随请求带来的最近聊天记录渲染成临时上下文。

    这层上下文只用于补齐“数据库历史暂时不可用、或前端刚收到回复还没刷新历史详情”
    之间的空窗。它不会替代 MySQL 持久化历史，也不会被当成外部事实来源。

    history_messages 是本轮已经作为原生 role 发送的历史：与它逐字相同的回显消息
    会被丢弃，避免同一段正文在同一个请求里出现两次，并多付一次 cache miss。
    """
    normalized = _normalize_client_messages(messages or [])
    normalized = _drop_duplicate_current_query(normalized, current_query=current_query)
    normalized = _drop_messages_already_in_history(normalized, history_messages or [])
    if not normalized:
        return ""

    lines = [
        "临时会话上下文：",
        "以下消息来自当前工作台页面已显示的最近聊天记录，只用于理解用户追问、选项回复或省略表达；不要把它当成外部实时事实。",
        "最近消息：",
    ]
    for item in normalized[-MAX_CLIENT_CONTEXT_MESSAGES:]:
        lines.append(f"- {_role_label(item['role'])}：{item['content']}")
    return _trim_context_lines(lines)


def _normalize_client_messages(messages: list[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in messages:
        role = str(_read_message_field(item, "role") or "").strip().lower()
        content = str(_read_message_field(item, "content") or "").strip()
        if role not in ALLOWED_CLIENT_CONTEXT_ROLES or not content:
            continue
        result.append({"role": role, "content": _compact_message_text(content)})
    return result


def _read_message_field(item: Any, field_name: str) -> Any:
    if isinstance(item, dict):
        return item.get(field_name)
    return getattr(item, field_name, None)


def _drop_duplicate_current_query(messages: list[dict[str, str]], *, current_query: str) -> list[dict[str, str]]:
    query = _compact_message_text(current_query)
    if not query or not messages:
        return messages
    last = messages[-1]
    if last["role"] == "user" and _compact_message_text(last["content"]) == query:
        return messages[:-1]
    return messages


def _drop_messages_already_in_history(
    messages: list[dict[str, str]],
    history_messages: list[dict[str, str]],
) -> list[dict[str, str]]:
    """丢掉已经在原生历史里逐字出现过的回显消息。

    只丢弃能完全对上的（role + 紧凑化正文），历史里没有的（例如尚未落库的乐观消息、
    数据库历史暂不可用）仍然保留，所以不会因此丢信息。
    """
    if not messages or not history_messages:
        return messages
    known = {
        (
            str(item.get("role") or "").strip().lower(),
            _compact_message_text(str(item.get("content") or "")),
        )
        for item in history_messages
        if isinstance(item, dict)
    }
    return [item for item in messages if (item["role"], item["content"]) not in known]


def _compact_message_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _trim_context_lines(lines: list[str]) -> str:
    rendered: list[str] = []
    used_chars = 0
    for line in lines:
        next_chars = len(line) + 1
        if rendered and used_chars + next_chars > MAX_CLIENT_CONTEXT_CHARS:
            rendered.append("...（临时会话上下文已截断）")
            break
        rendered.append(line)
        used_chars += next_chars
    return "\n".join(rendered)


def _role_label(role: str) -> str:
    if role == "user":
        return "用户"
    if role == "assistant":
        return "助手"
    return "系统"
