from __future__ import annotations

import json
import re

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
VISIBLE_ANSI_TOKEN_RE = re.compile(r"\[(?:\d{1,3}(?:;\d{1,3})*)m")
BOX_DRAWING_RE = re.compile(r"[┏┓┗┛┣┫┳┻╋━│─═]+")
PANEL_HEADER_RE = re.compile(r"^\s*(Message|Response|Input|Output)(?:\s*\(\d+(?:\.\d+)?s\))?\s*$", re.IGNORECASE)
INLINE_RESPONSE_MARKER_RE = re.compile(r"\bResponse(?:\s*\(\d+(?:\.\d+)?s\))?\b", re.IGNORECASE)
INTERNAL_PROMPT_MARKERS = (
    "请根据下面已经完成的执行结果",
    "不要声称自己又查询了新的实时数据",
    "用户原始问题：",
    "任务计划：",
    "执行观察：",
    "识别意图：",
    "风险等级：",
    "建议步骤：",
    "没有调用实时工具",
)


def summarize_text(text: str, max_chars: int = 1000) -> str:
    """把多余空白压平后截断，便于日志、历史和终端输出复用。"""
    compact = " ".join(text.split())
    return compact[:max_chars]


def sanitize_reasoning_text(text: str, max_chars: int = 8000) -> tuple[str, bool]:
    """清理思考文本并保留换行结构，超长时按段落边界截断。"""
    cleaned = sanitize_response_text(text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if len(cleaned) <= max_chars:
        return cleaned, False
    truncated = cleaned[:max_chars]
    paragraph_break = truncated.rfind("\n\n")
    if paragraph_break > max_chars // 2:
        truncated = truncated[:paragraph_break].rstrip()
    return truncated, True


def sanitize_response_text(text: str) -> str:
    """去掉终端/UI 不应展示的格式噪音，例如 ANSI 码和框线字符。"""
    cleaned = ANSI_ESCAPE_RE.sub("", text or "")
    cleaned = VISIBLE_ANSI_TOKEN_RE.sub("", cleaned)
    cleaned = _normalize_panel_dump_text(cleaned)
    if any(marker in cleaned for marker in INTERNAL_PROMPT_MARKERS) or INLINE_RESPONSE_MARKER_RE.search(cleaned):
        extracted = _extract_after_response_marker(cleaned)
        if extracted:
            cleaned = extracted
        else:
            cleaned = _drop_internal_prompt_lines(cleaned)
    lines: list[str] = []
    in_fenced_code = False
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if stripped.startswith("```"):
            lines.append(stripped)
            in_fenced_code = not in_fenced_code
            continue
        if in_fenced_code:
            lines.append(line.rstrip())
            continue
        if PANEL_HEADER_RE.match(stripped):
            continue
        if stripped.replace("|", "").replace(" ", "") == "":
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def _normalize_panel_dump_text(text: str) -> str:
    """清理 rich/终端面板框线，同时完整保留 Markdown 围栏代码块。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    in_fenced_code = False
    for line in normalized.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            lines.append(line.rstrip())
            in_fenced_code = not in_fenced_code
            continue
        if in_fenced_code:
            # 围栏代码块可能承载 ASCII/Unicode 架构图，框线和对齐空格都属于正文，不能清洗。
            lines.append(line.rstrip())
            continue
        if not BOX_DRAWING_RE.search(line):
            lines.append(line.rstrip())
            continue
        # 围栏外的框线来自 rich 终端面板。拆成独立文本行，兼容历史面板转储格式。
        lines.extend(
            segment.rstrip()
            for segment in BOX_DRAWING_RE.sub("\n", line).splitlines()
            if segment.strip()
        )
    return "\n".join(lines)


def _extract_after_response_marker(text: str) -> str | None:
    match = re.search(r"^\s*Response(?:\s*\(\d+(?:\.\d+)?s\))?\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
    if match:
        tail = text[match.end():].strip()
        return tail or None
    inline_match = INLINE_RESPONSE_MARKER_RE.search(text)
    if not inline_match:
        return None
    tail = text[inline_match.end():].strip()
    return tail or None


def _drop_internal_prompt_lines(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(marker in stripped for marker in INTERNAL_PROMPT_MARKERS):
            continue
        if re.match(r"^\d+\.", stripped):
            continue
        if PANEL_HEADER_RE.match(stripped):
            continue
        if stripped.startswith("- 识别意图") or stripped.startswith("- 风险等级"):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def format_history_text(rows: list) -> str:
    """把任务历史或工具日志渲染成适合 CLI 阅读的纯文本。"""
    if not rows:
        return "暂无记录。"
    lines = []
    for item in rows:
        created_at = getattr(item, "created_at", "")
        status = getattr(item, "status", "")
        if hasattr(item, "tool_name"):
            lines.append(
                f"[{created_at}] #{item.id} {item.tool_name} {status} "
                f"{item.duration_ms}ms - {summarize_text(item.output_summary or item.error_message or '', 120)}"
            )
        else:
            lines.append(
                f"[{created_at}] #{item.id} {item.task_type} {status} - "
                f"{summarize_text(item.user_input, 120)}"
            )
    return "\n".join(lines)


def format_memories_text(rows: list) -> str:
    """把长期记忆渲染成适合 CLI 查看的纯文本。"""
    if not rows:
        return "暂无长期记忆。"
    lines = []
    for item in rows:
        lines.append(f"[{item.created_at}] #{item.id} {item.source} - {summarize_text(item.memory_text, 160)}")
    return "\n".join(lines)


def format_doctor_text(checks: list[dict[str, str]]) -> str:
    """把 doctor 检查结果渲染成短文本。"""
    labels = {"ok": "OK", "warn": "WARN", "error": "ERROR"}
    return "\n".join(
        f"[{labels.get(check['status'], check['status'].upper())}] {check['name']}: {check['detail']}"
        for check in checks
    )


def redact_config(config: dict) -> dict:
    """把配置里的敏感值打码，避免 CLI 或 API 直接泄露密钥。"""
    redacted = json.loads(json.dumps(config))
    for section, values in redacted.items():
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if value and _looks_sensitive_key(str(key)):
                values[key] = "***"
    return redacted


def _looks_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    if any(token in normalized for token in ["api_key", "secret", "password", "authorization"]):
        return True
    return normalized == "token" or normalized.endswith("_token")
