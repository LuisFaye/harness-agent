"""从会话中的用户消息提取仍然生效的回答格式约束。"""

from __future__ import annotations

import re
from typing import Any


_CHAR_LIMIT_PATTERNS = (
    re.compile(r"(?:接下来|以后|后续).*?(?:回答|回复).*?(?:控制在|不超过|最多|限制在)\s*(\d{1,4})\s*个?字"),
    re.compile(r"(?:回答|回复).*?(?:控制在|不超过|最多|限制在)\s*(\d{1,4})\s*个?字"),
)
_CLEAR_LIMIT_PATTERN = re.compile(r"(?:取消|不用|不再).{0,8}(?:字数|长度|简短|限制)")
_PERSISTENT_FORMAT_PATTERN = re.compile(r"(?:接下来|以后|后续).{0,12}(?:回答|回复|输出).{0,12}(?:格式|形式)")
_CLEAR_FORMAT_PATTERN = re.compile(r"(?:取消|不用|不再).{0,8}(?:markdown|md|json|表格).{0,8}(?:格式|限制)?", re.IGNORECASE)
_FORMAT_PATTERNS = (
    (
        "markdown",
        re.compile(
            r"(?:用|使用|以|按|改成|转成|整理成|输出为?|回复成?|发我|给我).{0,10}(?:markdown|\.md|md\s*格式|md\s*文件)"
            r"|(?:markdown|\.md|md\s*格式|md\s*文件).{0,10}(?:发我|给我|输出|回复|整理)",
            re.IGNORECASE,
        ),
    ),
    (
        "json",
        re.compile(
            r"(?:用|使用|以|按|改成|转成|整理成|输出为?|回复成?).{0,10}json"
            r"|json.{0,10}(?:格式|输出|回复)",
            re.IGNORECASE,
        ),
    ),
    ("table", re.compile(r"(?:用|使用|以|按|改成|整理成|输出为?).{0,10}(?:表格|表形式)")),
)
_FALSE_FILE_CAPABILITY_PATTERN = re.compile(
    r"(?:没有|不支持|无法|不能).{0,30}(?:文件|markdown|md).{0,30}(?:发送|提供|生成|下载|功能)"
    r"|(?:没有|不支持|无法|不能).{0,30}(?:发送|提供|生成|下载).{0,30}(?:文件|markdown|md)",
    re.IGNORECASE,
)
_UNNECESSARY_FORMAT_CLARIFICATION_PATTERN = re.compile(
    r"(?:先确认|需要确认|是指|请告诉我|具体要生成什么).{0,80}(?:markdown|\.md|md\s*格式|md\s*文件)"
    r"|(?:markdown|\.md|md\s*格式|md\s*文件).{0,80}(?:先确认|需要确认|是指|请告诉我|具体要生成什么)",
    re.IGNORECASE,
)
_FORMAT_ONLY_REQUEST_PATTERN = re.compile(
    r"^(?:请|麻烦)?(?:把|将)?(?:刚才|上面|上述|之前|前面)?(?:的)?"
    r"(?:内容|回答|回复|结果)?(?:用|使用|以|按|整理成|改成|转成)?"
    r"(?:markdown|\.md|md\s*格式|md\s*文件)(?:格式|文件)?"
    r"(?:发我|给我|输出|回复|整理)?[。！!？?，,]*$",
    re.IGNORECASE,
)


def extract_response_constraints(messages: list[Any], current_query: str = "") -> list[str]:
    """按时间顺序读取用户消息，后出现的约束覆盖前面的约束。"""
    char_limit = extract_response_char_limit(messages, current_query=current_query)
    response_format = extract_response_format(messages, current_query=current_query)
    constraints = []
    if char_limit is not None:
        constraints.append(f"最终回答不得超过 {char_limit} 个汉字；这是用户明确要求的持续约束，优先于默认的展开说明。")
    if response_format == "markdown":
        constraints.append(
            "最终回答必须直接给出完整、可渲染的 Markdown 正文；工作台会提供“下载 .md”按钮，"
            "不得声称无法发送或生成 Markdown 文件，也不要让用户手动复制保存；"
            "不要用 ```markdown 围栏包住整份文档，不要添加确认反问或格式说明。"
        )
    elif response_format == "json":
        constraints.append("最终回答必须是合法 JSON，不要在 JSON 前后添加解释文字或 Markdown 代码围栏。")
    elif response_format == "table":
        constraints.append("最终回答必须使用清晰的 Markdown 表格呈现主要内容。")
    return constraints


def extract_response_format(messages: list[Any], current_query: str = "") -> str | None:
    """识别当前请求以及用户明确要求持续生效的输出格式。"""
    response_format: str | None = None
    for item in messages:
        if _message_role(item) != "user":
            continue
        text = " ".join(_message_content(item).split())
        if _CLEAR_FORMAT_PATTERN.search(text):
            response_format = None
        elif _PERSISTENT_FORMAT_PATTERN.search(text):
            response_format = _detect_response_format(text) or response_format
    current_format = _detect_response_format(" ".join(str(current_query or "").split()))
    return current_format or response_format


def repair_format_response(
    text: str,
    response_format: str | None,
    previous_assistant: str = "",
    current_query: str = "",
) -> str:
    """清理 Markdown 外层围栏；模型错误否认下载能力时恢复上一条完整回答。"""
    value = str(text or "").strip()
    if response_format != "markdown":
        return value
    fallback = str(previous_assistant or "").strip()
    normalized_query = " ".join(str(current_query or "").split())
    # “md 格式发我”是在修改上一条回答的交付格式，不是开启一个需要重新澄清的新任务。
    # 直接复用上一条完整内容，避免依赖模型可能变化的反问话术。
    if fallback and _FORMAT_ONLY_REQUEST_PATTERN.fullmatch(normalized_query):
        return _unwrap_markdown_document(fallback)
    if _FALSE_FILE_CAPABILITY_PATTERN.search(value) or _UNNECESSARY_FORMAT_CLARIFICATION_PATTERN.search(value):
        return fallback or value
    return _unwrap_markdown_document(value)


def extract_response_char_limit(messages: list[Any], current_query: str = "") -> int | None:
    """提取当前真正生效的字数上限，供提示词和最终响应兜底共同使用。"""
    char_limit: int | None = None
    user_texts = [_message_content(item) for item in messages if _message_role(item) == "user"]
    if current_query:
        user_texts.append(str(current_query))
    for text in user_texts:
        normalized = " ".join(text.split())
        if _CLEAR_LIMIT_PATTERN.search(normalized):
            char_limit = None
            continue
        for pattern in _CHAR_LIMIT_PATTERNS:
            matched = pattern.search(normalized)
            if matched:
                char_limit = max(1, min(2000, int(matched.group(1))))
                break
    return char_limit


def enforce_response_char_limit(text: str, char_limit: int | None) -> str:
    """模型忽略明确字数要求时，输出前做最后一道确定性兜底。"""
    value = str(text or "").strip()
    if not char_limit or len(value) <= char_limit:
        return value
    # 截断 Markdown 很容易留下半个链接或未闭合加粗，因此超限时先转成简洁纯文本。
    value = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", value)
    value = re.sub(r"[*_`#>|]", "", value)
    value = " ".join(value.split())
    if len(value) <= char_limit:
        return value
    if char_limit == 1:
        return "…"
    return value[: char_limit - 1].rstrip("，,。；;：: ") + "…"


def _detect_response_format(text: str) -> str | None:
    for name, pattern in _FORMAT_PATTERNS:
        if pattern.search(text):
            return name
    return None


def _unwrap_markdown_document(text: str) -> str:
    """提取模型放在 markdown 围栏中的正文，同时保留正文内部的代码围栏。"""
    matched = re.search(r"```(?:markdown|md)\s*\n", text, re.IGNORECASE)
    if not matched:
        return text
    end = text.rfind("\n```")
    if end <= matched.end():
        return text
    document = text[matched.end():end].strip()
    return document if document.count("```") % 2 == 0 else text


def _message_role(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("role") or "").lower()
    return str(getattr(item, "role", "") or "").lower()


def _message_content(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("content") or "")
    return str(getattr(item, "content", "") or "")
