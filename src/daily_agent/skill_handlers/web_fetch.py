"""网页正文读取 Skill。"""

from __future__ import annotations

import json
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from daily_agent.errors import DailyAgentError
from daily_agent.skill_runtime import (
    SKILL_STATUS_ERROR,
    SKILL_STATUS_SUCCESS,
    SkillExecutionContext,
    SkillRunResult,
)


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARACTERS = 30_000


def run(*, context: SkillExecutionContext) -> SkillRunResult:
    inputs = dict(context.inputs)
    url = str(inputs.get("url") or "").strip()
    if not url:
        return _error_result(inputs, "请提供需要读取的完整网页 URL。", "缺少网页 URL")
    try:
        _validate_url(url)
        response = _request(url)
        final_url = str(response.url)
        _validate_url(final_url)
        content_type = str(response.headers.get("content-type") or "").lower()
        if "text/html" in content_type:
            title, text = _extract_html(response.text)
        elif content_type.startswith("text/") or "json" in content_type:
            title = ""
            text = response.text
        else:
            raise DailyAgentError(f"当前不支持读取内容类型：{content_type or '未知'}")
        compact_text = _normalize_text(text)
        if not compact_text:
            raise DailyAgentError("网页没有提取到可用正文。")
        truncated = len(compact_text) > MAX_TEXT_CHARACTERS
        compact_text = compact_text[:MAX_TEXT_CHARACTERS]
        payload = {
            "requested_url": url,
            "final_url": final_url,
            "title": title,
            "fetched_at": datetime.now().astimezone().isoformat(),
            "content_type": content_type,
            "truncated": truncated,
            "text": compact_text,
        }
        output = json.dumps(payload, ensure_ascii=False)
        return SkillRunResult(
            status=SKILL_STATUS_SUCCESS,
            response_text=output,
            stop_reason="网页正文读取完成",
            inputs={"url": url},
            output_text=output,
            observation=payload,
            clear_session=True,
        )
    except httpx.HTTPStatusError as exc:
        return _error_result(inputs, _http_status_message(url, exc), "网页正文读取失败")
    except (DailyAgentError, httpx.HTTPError, OSError, ValueError) as exc:
        return _error_result(inputs, f"网页读取失败：{exc}", "网页正文读取失败")


def _request(url: str) -> httpx.Response:
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        response = client.get(
            url,
            headers={
                "User-Agent": "SB-Agent/0.1 web-fetch",
                "Accept": "text/html,text/plain,application/json;q=0.8",
            },
        )
        response.raise_for_status()
        content_length = int(response.headers.get("content-length") or 0)
        if content_length > MAX_RESPONSE_BYTES or len(response.content) > MAX_RESPONSE_BYTES:
            raise DailyAgentError("网页内容超过 2MB 限制。")
        return response


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DailyAgentError("网页 URL 必须使用 http 或 https。")
    if parsed.username or parsed.password:
        raise DailyAgentError("网页 URL 不能包含用户名或密码。")


def _extract_html(html: str) -> tuple[str, str]:
    parser = _ReadableHtmlParser()
    parser.feed(html)
    return parser.title, "\n".join(parser.text_parts)


class _ReadableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.text_parts: list[str] = []
        self._ignored_depth = 0
        self._inside_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag == "title":
            self._inside_title = True
        if tag in {"p", "div", "section", "article", "main", "li", "h1", "h2", "h3", "br"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        if tag == "title":
            self._inside_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._inside_title:
            self.title = f"{self.title} {text}".strip()
            return
        self.text_parts.append(text)


def _normalize_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _http_status_message(url: str, exc: httpx.HTTPStatusError) -> str:
    """把 4xx/5xx 转成模型能直接行动的中文说明。

    原始 httpx 文案（英文 + MDN 链接）会让模型只懂得重试同一个 URL。这里改成：
    明确给出状态码，并提示文档站可能提供的 llms.txt 入口。
    刻意不做自动探测：多打一次请求换不来收益，而且正文一旦被自动拼接进结果，
    就会改写已发出的工具消息，反而弄丢后续轮次的 KV cache 前缀。
    """
    status = exc.response.status_code if exc.response is not None else 0
    host = urlparse(url).hostname or ""
    lines = [f"网页读取失败：HTTP {status}：{url}"]
    if status == 404:
        lines.append("这个 URL 不存在或已迁移；不要重复请求同一个 URL，也不要加查询参数绕过重复调用检测。")
        if host.startswith("docs.") or host.startswith("developer."):
            lines.append(
                f"该站是文档站；如果它提供 llms.txt，可尝试读取 https://{host}/llms.txt，"
                "再从索引里选择真实文档 URL。"
            )
    return "\n".join(lines)


def _error_result(inputs: dict[str, Any], response_text: str, stop_reason: str) -> SkillRunResult:
    return SkillRunResult(
        status=SKILL_STATUS_ERROR,
        response_text=response_text,
        stop_reason=stop_reason,
        inputs=inputs,
        observation={"error": response_text},
        clear_session=True,
    )
