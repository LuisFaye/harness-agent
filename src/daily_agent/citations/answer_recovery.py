"""恢复模型终态回答的常见格式偏差，不推测缺失内容或来源关系。"""

from __future__ import annotations

import json
import re
from typing import Any

from daily_agent.citations.models import AgentAnswerBlock, AgentStructuredAnswer

_THINKING_BLOCK_RE = re.compile(
    r"<(?:think|thinking|reasoning|analysis)>(.*?)</(?:think|thinking|reasoning|analysis)>",
    re.IGNORECASE | re.DOTALL,
)
_ANSWER_FIELD_KEYS = ("markdown", "answer", "response", "text", "content")
_TOOL_CALL_MARKERS = ('"tool_calls"', '"tool_call_id"', '"arguments"', '"parameters"')


def complete_answer_blocks(value: Any) -> tuple[list[AgentAnswerBlock], str]:
    """只从已完成的输出恢复正文；普通文本不继承任何未经确认的引用。"""
    if isinstance(value, AgentStructuredAnswer):
        return [item.model_copy(deep=True) for item in value.blocks if item.markdown.strip()], "structured"
    if isinstance(value, str):
        text = strip_thinking_blocks(value).strip()
        if not text:
            return [], "empty"
        candidate = strip_json_fence(text)
        payload = None
        try:
            payload = json.loads(candidate, strict=False)
        except (ValueError, TypeError):
            payload = _repair_truncated_json(candidate)
            if payload is None:
                # 结构化 JSON 无法解析时，把非空内容原样交给用户，而不是整段丢弃。
                # 只有明确像工具调用参数等机器内容时才不展示。
                cleaned = candidate.strip()
                if not cleaned:
                    return [], "empty"
                if _looks_like_tool_call_payload(cleaned):
                    return [], "invalid_structure"
                return [AgentAnswerBlock(markdown=cleaned, source_ids=[])], "plain_text"
        if isinstance(payload, str):
            cleaned = strip_thinking_blocks(payload).strip()
            return ([AgentAnswerBlock(markdown=cleaned, source_ids=[])] if cleaned else []), "plain_text"
        value = payload
    if isinstance(value, dict):
        raw_blocks = value.get("blocks")
        if isinstance(raw_blocks, list):
            blocks = _blocks_from_list(raw_blocks)
            if blocks:
                return blocks, "structured"
        # 只兼容明确的回答字段，不能把任意工具 JSON 序列化成用户答案。
        # 先取可读正文，再考虑 reasoning/tool 结构，避免丢失 content 等有效回答。
        for key in _ANSWER_FIELD_KEYS:
            content = value.get(key)
            if isinstance(content, str) and content.strip():
                return [AgentAnswerBlock(markdown=content.strip(), source_ids=[])], "answer_field"
        return [], "invalid_structure"
    elif isinstance(value, list):
        blocks = _blocks_from_list(value)
        if blocks:
            return blocks, "structured"
        return [], "invalid_structure"
    return [], "invalid_structure"


def _blocks_from_list(raw_blocks: list[Any]) -> list[AgentAnswerBlock]:
    blocks: list[AgentAnswerBlock] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict) or not isinstance(raw.get("markdown"), str):
            continue
        markdown = raw["markdown"].strip()
        if not markdown:
            continue
        ids = raw.get("source_ids")
        source_ids = [item for item in ids if isinstance(item, str) and item.strip()][:3] if isinstance(ids, list) else []
        blocks.append(AgentAnswerBlock(markdown=markdown, source_ids=source_ids))
    return blocks


def _repair_truncated_json(text: str) -> Any | None:
    """尝试闭合被截断的 JSON 对象或数组，成功后返回解析结果。"""
    candidate = text.strip()
    if not candidate or candidate[0] not in "{[":
        return None
    in_string = False
    escape = False
    stack: list[str] = []
    for character in candidate:
        if escape:
            escape = False
            continue
        if character == "\\" and in_string:
            escape = True
            continue
        if character == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if character == "{":
            stack.append("}")
        elif character == "[":
            stack.append("]")
        elif character in "}]" and stack and stack[-1] == character:
            stack.pop()
    if not stack and not in_string:
        return None
    suffix = ""
    if in_string:
        suffix += '"'
    else:
        # 截断时对象/数组末尾可能残留逗号，闭合前先移除。
        candidate = candidate.rstrip().rstrip(",")
    suffix += "".join(reversed(stack))
    try:
        return json.loads(candidate + suffix, strict=False)
    except (ValueError, TypeError):
        return None


def _repair_truncated_blocks_json(text: str) -> dict[str, Any] | None:
    """兼容旧调用：只修复含 blocks 数组的对象。"""
    candidate = text.strip()
    if not candidate.startswith("{") or '"blocks"' not in candidate:
        return None
    payload = _repair_truncated_json(candidate)
    if isinstance(payload, dict) and isinstance(payload.get("blocks"), list):
        return payload
    return None


def strip_json_fence(value: str) -> str:
    """只移除包裹 JSON 的代码围栏，不破坏普通回答中的代码块。"""
    text = value.strip()
    lines = text.splitlines()
    if not lines:
        return text
    opening = lines[0].strip().lower()
    if not opening.startswith("```"):
        return text
    inner_lines = lines[1:]
    if inner_lines and inner_lines[-1].strip() == "```":
        inner_lines = inner_lines[:-1]
    inner = "\n".join(inner_lines).strip()
    language = opening[3:].strip()
    if language == "json" or (not language and inner.startswith(("{", "[", '"'))):
        return inner
    return text


def strip_thinking_blocks(value: str) -> str:
    """去掉模型常夹带在正文里的思考标签，保留剩余可读内容。"""
    text = str(value or "")
    previous = None
    while text != previous:
        previous = text
        text = _THINKING_BLOCK_RE.sub("", text)
    return text


def _looks_like_tool_call_payload(text: str) -> bool:
    """判断一段无法解析的文本是否更接近工具调用参数而非用户答案。"""
    lower = text.lower()
    return any(marker in lower for marker in _TOOL_CALL_MARKERS)
