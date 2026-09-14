"""把最终 Markdown 拆成可稳定挂载引用的语义块。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


@dataclass(frozen=True)
class AnswerBlock:
    block_id: str
    block_type: str
    text: str


_LIST_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+(.+)$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")


def parse_answer_blocks(markdown: str) -> list[AnswerBlock]:
    """只解析第一期承诺支持的普通段落和列表项。

    解析器刻意跳过代码块、标题、表格和分隔线，避免把代码输出或结构性文本
    误判成外部事实。每个块同时包含顺序和内容摘要；前端还会用 answer_quote
    二次匹配，因此单纯的段落插入不会把旧引用错误挂到另一个段落。
    """
    raw_blocks: list[tuple[str, str]] = []
    paragraph_lines: list[str] = []
    in_fence = False

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        plain = markdown_plain_text(" ".join(paragraph_lines))
        paragraph_lines.clear()
        if len(plain) >= 8:
            raw_blocks.append(("p", plain))

    for raw_line in str(markdown or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_paragraph()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            flush_paragraph()
            continue
        if _HEADING.match(raw_line) or stripped in {"---", "***", "___"}:
            flush_paragraph()
            continue
        if _TABLE_SEPARATOR.match(raw_line) or (stripped.startswith("|") and stripped.endswith("|")):
            flush_paragraph()
            continue
        list_match = _LIST_ITEM.match(raw_line)
        if list_match:
            flush_paragraph()
            plain = markdown_plain_text(list_match.group(1))
            if len(plain) >= 8:
                raw_blocks.append(("li", plain))
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            continue
        paragraph_lines.append(stripped)
    flush_paragraph()

    blocks: list[AnswerBlock] = []
    for index, (block_type, text) in enumerate(raw_blocks, 1):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        blocks.append(AnswerBlock(block_id=f"{block_type}-{index:02d}-{digest}", block_type=block_type, text=text))
    return blocks


def markdown_plain_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return normalize_text(text)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
