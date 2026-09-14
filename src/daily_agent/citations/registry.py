"""从当前百炼联网搜索 Tool 结果建立只读来源注册表。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from daily_agent.citations.models import CitationChunk, CitationSource, content_hash


BAILIAN_WEB_SEARCH_TOOL = "mcp_web_search_bailian_web_search"
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


class WebCitationRegistry:
    """每次 run 独占一份注册表，只接收当前已确认的搜索 Tool。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id or "")
        self._sources: dict[str, CitationSource] = {}
        self._chunks: dict[str, CitationChunk] = {}
        self._snippet_chunks: dict[str, str] = {}
        self._lock = Lock()

    @property
    def has_sources(self) -> bool:
        with self._lock:
            return bool(self._sources)

    def capture(self, tool_name: str, result: Any) -> list[str]:
        if str(tool_name or "").lower() != BAILIAN_WEB_SEARCH_TOOL:
            return []
        payload = _parse_result(result)
        pages = payload.get("pages") if isinstance(payload, dict) else None
        if not isinstance(pages, list):
            return []
        retrieved_at = datetime.now(timezone.utc).isoformat()
        captured_source_ids: list[str] = []
        with self._lock:
            for page in pages[:20]:
                if not isinstance(page, dict):
                    continue
                canonical_url = canonicalize_web_url(page.get("url"))
                if not canonical_url:
                    continue
                source_id = _stable_id("src", f"{self.run_id}|{canonical_url}")
                title = str(page.get("title") or urlsplit(canonical_url).hostname or canonical_url).strip()[:500]
                snippet = str(page.get("snippet") or page.get("content") or "").strip()[:4000]
                source = CitationSource(
                    source_id=source_id,
                    title=title,
                    url=canonical_url,
                    domain=str(urlsplit(canonical_url).hostname or ""),
                    provider="bailian",
                    tool_name=BAILIAN_WEB_SEARCH_TOOL,
                    retrieved_at=retrieved_at,
                    content_level="snippet" if snippet else "metadata",
                    content_hash=content_hash(snippet) if snippet else None,
                )
                self._sources[source_id] = source
                if source_id not in captured_source_ids:
                    captured_source_ids.append(source_id)
                if snippet:
                    chunk_id = _stable_id("chk", f"{source_id}|snippet|{snippet}")
                    self._chunks[chunk_id] = CitationChunk(
                        chunk_id=chunk_id,
                        source_id=source_id,
                        text=snippet,
                        locator={"type": "search_snippet"},
                        content_hash=content_hash(snippet),
                    )
                    self._snippet_chunks[source_id] = chunk_id
        return captured_source_ids

    def render_model_context(self, source_ids: list[str], *, limit: int = 12) -> str:
        """把服务端分配的来源 ID 附加给搜索结果，供同一次 Agent 生成直接引用。

        只回传 source_id/title/url：正文摘要已经在同一条工具结果里发送过一次，
        在这里再内联一遍等于把同一段文字写两次进公共前缀，多付一次 cache miss。
        """
        selected: list[dict[str, str]] = []
        with self._lock:
            for source_id in source_ids:
                source = self._sources.get(source_id)
                if not source:
                    continue
                selected.append(
                    {
                        "source_id": source.source_id,
                        "title": source.title,
                        "url": source.url,
                    }
                )
                if len(selected) >= max(1, int(limit)):
                    break
        if not selected:
            return ""
        return (
            "引用来源注册表（最终回答的 source_ids 只能从下面选择；正文摘要见上一条工具结果，"
            "不要自行生成来源 ID、标题或 URL）：\n"
            + json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
        )

    def valid_source_ids(self, source_ids: list[str], *, limit: int = 3) -> list[str]:
        """保持模型给出的顺序，只返回当前运行注册表中真实存在的来源 ID。"""
        selected: list[str] = []
        with self._lock:
            for source_id in source_ids:
                normalized = str(source_id or "")
                if normalized in self._sources and normalized not in selected:
                    selected.append(normalized)
                if len(selected) >= max(1, int(limit)):
                    break
        return selected

    def snapshot(self) -> tuple[list[CitationSource], list[CitationChunk], dict[str, str]]:
        with self._lock:
            return (
                [item.model_copy(deep=True) for item in self._sources.values()],
                [item.model_copy(deep=True) for item in self._chunks.values()],
                dict(self._snippet_chunks),
            )


def canonicalize_web_url(value: Any) -> str | None:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)
    netloc = host if not port or default_port else f"{host}:{port}"
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
        ],
        doseq=True,
    )
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", query, ""))


def _parse_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        if isinstance(result.get("structured_content"), dict):
            return result["structured_content"]
        return result
    if not isinstance(result, str):
        return {}
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"
