"""Agent Loop 使用的短文本和用户可见工具名称。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


FRIENDLY_TOOL_NAMES = {
    "daily_report": "生成工作日报",
    "project_inspector": "梳理项目结构",
    "official_document": "生成红头公文",
    "web_fetch": "读取网页正文",
    "read_run_evidence": "读取完整证据",
}


def coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def compact_loop_text(value: str, limit: int) -> str:
    compact = " ".join(str(value or "").split())
    return compact if len(compact) <= limit else f"{compact[:limit]}…"


def forced_tool_retry_query(query: str) -> str:
    return (
        f"{query}\n\n"
        "系统重试要求：这是实时或外部信息请求。上一轮没有产生本轮工具证据，"
        "不能复用历史回答。必须调用当前可用的对应工具；如果没有可用工具，直接明确说明。"
    )


def forced_format_retry_query(query: str) -> str:
    return (
        f"{query}\n\n"
        "系统重试要求：最终回答必须严格输出为结构化 JSON 对象 "
        "{\"blocks\":[{\"markdown\":\"...\",\"source_ids\":[]}]}，"
        "除该对象外不要输出任何其他文字、Markdown 代码围栏或思考过程。"
    )


def is_structured_format_failure(response_text: str) -> bool:
    text = str(response_text or "")
    return "无法识别的回答格式" in text or "回答格式未完整结束" in text


def friendly_tool_name(tool_name: str) -> str:
    return FRIENDLY_TOOL_NAMES.get(tool_name, tool_name)


def local_response_for_simple_input(query: str) -> str | None:
    # 只忽略首尾标点和空白，避免把「你好，帮我删文件」误判成本地问候。
    normalized = "".join(str(query or "").strip().lower().split()).strip("。，！？,.!?")
    if normalized in {"你好", "您好", "hello", "hi", "hey", "nihao", "在吗", "在不在"}:
        return "你好，我在。你可以直接告诉我要处理什么任务。"
    today_queries = {
        "今天是几号", "今天几号", "今天日期", "今天是什么日期", "今天星期几",
        "今天周几", "今天是星期几", "今天是周几", "现在几号", "现在日期",
    }
    if normalized in today_queries:
        return format_relative_date_answer(0)
    if normalized in {"明天是几号", "明天几号", "明天星期几", "明天周几", "明天是星期几", "明天是周几"}:
        return format_relative_date_answer(1)
    if normalized in {"昨天是几号", "昨天几号", "昨天星期几", "昨天周几", "昨天是星期几", "昨天是周几"}:
        return format_relative_date_answer(-1)
    return None


def format_relative_date_answer(day_offset: int, now: datetime | None = None) -> str:
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    target = current + timedelta(days=day_offset)
    label = "今天" if day_offset == 0 else "明天" if day_offset > 0 else "昨天"
    return f"{label}是 {target:%Y-%m-%d}，{weekday_zh(target)}。"


def weekday_zh(value: datetime) -> str:
    return ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][value.weekday()]


def is_transient_model_error(response_text: str) -> bool:
    text = str(response_text or "").lower()
    return any(
        token in text
        for token in (
            "too busy",
            "temporarily unavailable",
            "rate limit",
            "timeout",
            "timed out",
            "429",
            "502",
            "503",
            "504",
            "服务繁忙",
            "暂时不可用",
            "请求超时",
        )
    )


def normalize_event_type(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    compact = "".join(char for char in raw.lower() if char.isalnum())
    aliases = {
        "runcontent": "run_content",
        "runcompleted": "run_completed",
        "runpaused": "run_paused",
        "runerror": "run_error",
        "runcancelled": "run_cancelled",
        "toolcallstarted": "tool_call_started",
        "toolcallcompleted": "tool_call_completed",
        "toolcallerror": "tool_call_error",
        "reasoningstarted": "reasoning_started",
        "reasoningcontentdelta": "reasoning_content_delta",
        "reasoningcompleted": "reasoning_completed",
    }
    return aliases.get(compact, raw.replace("-", "_").lower())
