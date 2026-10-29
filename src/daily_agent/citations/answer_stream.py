"""解析 Agent 流式生成的段落级结构化回答。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from daily_agent.citations.models import AgentAnswerBlock
from daily_agent.citations.answer_recovery import complete_answer_blocks


_BLOCKS_ARRAY = re.compile(r'"blocks"\s*:\s*\[')
logger = logging.getLogger(__name__)


class AgentAnswerStreamParser:
    """从尚未结束的 JSON 中逐个取出已经完整生成的回答块。

    模型输出是一个包含 ``blocks`` 数组的 JSON 对象。这里不等待整个对象闭合，
    而是在数组中的单个对象闭合后立即解析并交给上层，因此前端可以按段落展示
    Markdown 和来源。JSON 字符串里的换行、引号和 Unicode 转义由标准解码器处理，
    不使用正则直接截取正文，避免内容中的大括号破坏边界。
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._position = 0
        self._array_started = False
        self._decoder = json.JSONDecoder(strict=False)
        self._blocks: list[AgentAnswerBlock] = []
        self._streamed_text = ""
        self.complete = False

    @property
    def blocks(self) -> list[AgentAnswerBlock]:
        return [item.model_copy(deep=True) for item in self._blocks]

    def feed(self, chunk: str) -> list[AgentAnswerBlock]:
        self._buffer += str(chunk or "")
        return self._consume_complete_blocks()

    def take_text_delta(self) -> str:
        """返回结构化 JSON 中目前已经安全解码的 Markdown 增量。"""
        preview = _preview_answer_text(self._buffer)
        if not preview.startswith(self._streamed_text):
            return ""
        delta = preview[len(self._streamed_text):]
        self._streamed_text = preview
        return delta

    def finish(self, final_content: Any = None) -> list[AgentAnswerBlock]:
        """优先核对完整终态，再恢复流式正文；空格和半截片段不能挡住完整回答。"""
        self._consume_complete_blocks()
        self.complete = False
        for candidate in (final_content, self._buffer):
            blocks, mode = complete_answer_blocks(candidate)
            if blocks:
                if mode == "plain_text" and self._blocks:
                    # 流式阶段已解析出合法 blocks 时，不能被包含原始 JSON 的普通文本回退覆盖。
                    continue
                self.complete = True
                self._blocks = blocks
                if mode != "structured":
                    logger.warning("模型回答格式已兼容恢复：mode=%s blocks=%s", mode, len(blocks))
                return self.blocks
        # 完整 JSON 缺失时只保留安全解码的正文，未知引用保持为空。
        # 已有完整段落后面仍可能跟着半截段落，不能把用户已看到的尾段吞掉。
        preview = _preview_answer_text(self._buffer).strip()
        completed = join_answer_blocks(self._blocks)
        if preview and preview.startswith(completed):
            tail = preview[len(completed):].strip()
            if tail:
                self._blocks.append(AgentAnswerBlock(markdown=tail, source_ids=[]))
            elif self._blocks:
                # 流式已拿到若干完整 block，且没有正在输出的半截 markdown，只是外层 JSON 没收口。
                self.complete = True
        if not self._blocks:
            logger.warning(
                "模型回答无法恢复：stream_chars=%s final_type=%s final_chars=%s",
                len(self._buffer), type(final_content).__name__,
                len(final_content) if isinstance(final_content, str) else 0,
            )
        return self.blocks

    def _consume_complete_blocks(self) -> list[AgentAnswerBlock]:
        emitted: list[AgentAnswerBlock] = []
        if not self._array_started:
            match = _BLOCKS_ARRAY.search(self._buffer)
            if not match:
                return emitted
            self._array_started = True
            self._position = match.end()
        while True:
            self._skip_delimiters()
            if self._position >= len(self._buffer) or self._buffer[self._position] == "]":
                return emitted
            try:
                payload, end = self._decoder.raw_decode(self._buffer, self._position)
            except json.JSONDecodeError:
                return emitted
            self._position = end
            block = _validate_block(payload)
            if block is None:
                continue
            self._blocks.append(block)
            emitted.append(block.model_copy(deep=True))

    def _skip_delimiters(self) -> None:
        while self._position < len(self._buffer):
            character = self._buffer[self._position]
            if character.isspace() or character == ",":
                self._position += 1
                continue
            return


def finish_answer_stream(
    parser: AgentAnswerStreamParser,
    final_content: Any,
    emitted_blocks: list[AgentAnswerBlock],
    emit_block: Callable[[AgentAnswerBlock], None],
) -> str:
    """统一收口终态与已发出的段落，确保完整终态修正能同步到正文和引用。"""
    blocks = parser.finish(final_content)
    if emitted_blocks != blocks[:len(emitted_blocks)]:
        # 终态可能修正流式片段，必须通过现有 response_block.text 全量替换，
        # 不能按块数量直接跳过，导致新正文和旧引用错位。
        emitted_blocks.clear()
    for block in blocks[len(emitted_blocks):]:
        emit_block(block)
    return join_answer_blocks(emitted_blocks)


def join_answer_blocks(blocks: list[AgentAnswerBlock]) -> str:
    return "\n\n".join(item.markdown.strip() for item in blocks if item.markdown.strip())


def block_stream_delta(block: AgentAnswerBlock, *, has_previous: bool) -> str:
    markdown = block.markdown.strip()
    return f"\n\n{markdown}" if has_previous else markdown


def _validate_block(payload: Any) -> AgentAnswerBlock | None:
    if not isinstance(payload, dict):
        return None
    markdown = str(payload.get("markdown") or "").strip()
    raw_source_ids = payload.get("source_ids")
    source_ids = [str(item or "") for item in raw_source_ids[:3]] if isinstance(raw_source_ids, list) else []
    try:
        block = AgentAnswerBlock(markdown=markdown, source_ids=source_ids)
    except Exception:
        return None
    if not markdown:
        return None
    return block.model_copy(update={"markdown": markdown})


def _preview_answer_text(value: str) -> str:
    """只预览 blocks 数组里已完整编码的 Markdown 字符，避免把 JSON 语法展示给用户。"""
    array_match = _BLOCKS_ARRAY.search(value)
    if not array_match:
        return ""
    markdown_pattern = re.compile(r'"markdown"\s*:\s*"')
    parts: list[str] = []
    position = array_match.end()
    while True:
        match = markdown_pattern.search(value, position)
        if not match:
            break
        decoded, end, complete = _decode_json_string_prefix(value, match.end())
        if decoded:
            parts.append(decoded)
        position = end
        if not complete:
            break
    return "\n\n".join(parts)


def _decode_json_string_prefix(value: str, start: int) -> tuple[str, int, bool]:
    """解码 JSON 字符串的完整前缀，不提前输出半个转义序列或 Unicode 代理对。"""
    position = start
    safe_end = start
    while position < len(value):
        character = value[position]
        if character == '"':
            return json.loads(f'"{value[start:position]}"', strict=False), position + 1, True
        if character != "\\":
            position += 1
            safe_end = position
            continue
        if position + 1 >= len(value):
            break
        escape = value[position + 1]
        if escape != "u":
            if escape not in {'"', "\\", "/", "b", "f", "n", "r", "t"}:
                break
            position += 2
            safe_end = position
            continue
        hexadecimal = value[position + 2:position + 6]
        if len(hexadecimal) < 4 or not all(item in "0123456789abcdefABCDEF" for item in hexadecimal):
            break
        codepoint = int(hexadecimal, 16)
        position += 6
        if 0xD800 <= codepoint <= 0xDBFF:
            low_surrogate = value[position:position + 6]
            if (
                len(low_surrogate) < 6
                or not low_surrogate.startswith("\\u")
                or not all(item in "0123456789abcdefABCDEF" for item in low_surrogate[2:])
                or not 0xDC00 <= int(low_surrogate[2:], 16) <= 0xDFFF
            ):
                break
            position += 6
        safe_end = position
    raw_prefix = value[start:safe_end]
    return (json.loads(f'"{raw_prefix}"', strict=False) if raw_prefix else ""), safe_end, False
