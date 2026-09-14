"""保存长工具结果，并向模型提供可控的按需读取入口。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from daily_agent.context import estimate_text_tokens
from daily_agent.errors import DailyAgentError, MemoryError as DailyAgentMemoryError
from daily_agent.memory import MySQLMemoryStore


INLINE_TOKEN_LIMIT = 800
# 网页/文件正文通常需要模型直接阅读。提高内联阈值可减少一次证据回读，
# 但会增加当前请求输入量，实际 token 与缓存收益应以运行指标为准。
DOCUMENT_INLINE_TOKEN_LIMIT = 4000
# 搜索结果是一堆摘要拼成的 JSON，全文内联只会把噪声写进每一轮前缀，仍保持紧凑。
_DOCUMENT_TOOL_MARKERS = ("web_fetch", "fetch_page", "fetch_url", "read_url", "read_file")
PREVIEW_CHARACTER_LIMIT = 2000
EVIDENCE_CHUNK_CHARACTER_LIMIT = 3200
StoreFactory = Callable[[], MySQLMemoryStore]


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    run_id: str
    user_id: str
    session_id: str
    tool_name: str
    content_type: str
    raw_content: str
    character_count: int
    estimated_tokens: int


@dataclass(frozen=True)
class CompactedToolResult:
    model_content: Any
    evidence_id: str | None
    original_estimated_tokens: int
    injected_estimated_tokens: int


class EvidenceRepository:
    def __init__(self, store_factory: StoreFactory | None = None) -> None:
        self.store_factory = store_factory or MySQLMemoryStore

    def save(
        self,
        *,
        run_id: str,
        user_id: str,
        session_id: str,
        tool_name: str,
        raw_content: str,
        content_type: str,
    ) -> EvidenceRecord:
        evidence_id = f"ev-{uuid.uuid4().hex}"
        estimated_tokens = estimate_text_tokens(raw_content)
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO agent_run_evidence
                            (evidence_id, run_id, user_id, session_id, tool_name, content_type,
                             raw_content, character_count, estimated_tokens)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            evidence_id,
                            run_id,
                            user_id,
                            session_id,
                            tool_name,
                            content_type,
                            raw_content,
                            len(raw_content),
                            estimated_tokens,
                        ),
                    )
                connection.commit()
        except DailyAgentError:
            raise
        except Exception as exc:
            raise DailyAgentMemoryError(f"保存运行证据失败：{exc}") from exc
        return EvidenceRecord(
            evidence_id=evidence_id,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            tool_name=tool_name,
            content_type=content_type,
            raw_content=raw_content,
            character_count=len(raw_content),
            estimated_tokens=estimated_tokens,
        )

    def get(
        self,
        *,
        evidence_id: str,
        user_id: str,
        session_id: str,
    ) -> EvidenceRecord | None:
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT evidence_id, run_id, user_id, session_id, tool_name, content_type,
                               raw_content, character_count, estimated_tokens
                        FROM agent_run_evidence
                        WHERE evidence_id = %s AND user_id = %s AND session_id = %s
                        LIMIT 1
                        """,
                        (evidence_id, user_id, session_id),
                    )
                    row = cursor.fetchone()
        except DailyAgentError:
            raise
        except Exception as exc:
            raise DailyAgentMemoryError(f"读取运行证据失败：{exc}") from exc
        if not row:
            return None
        return EvidenceRecord(
            evidence_id=str(row["evidence_id"]),
            run_id=str(row["run_id"]),
            user_id=str(row["user_id"]),
            session_id=str(row["session_id"]),
            tool_name=str(row["tool_name"]),
            content_type=str(row["content_type"]),
            raw_content=str(row["raw_content"]),
            character_count=int(row["character_count"]),
            estimated_tokens=int(row["estimated_tokens"]),
        )


class ToolResultCompactor:
    """短结果保持原样，只有长结果成功持久化后才返回压缩预览。"""

    def __init__(
        self,
        repository: EvidenceRepository | None = None,
        document_inline_token_limit: int = DOCUMENT_INLINE_TOKEN_LIMIT,
    ) -> None:
        self.repository = repository or EvidenceRepository()
        self.document_inline_token_limit = max(1, int(document_inline_token_limit))

    def compact(
        self,
        result: Any,
        *,
        run_id: str,
        user_id: str,
        session_id: str,
        tool_name: str,
    ) -> CompactedToolResult:
        raw_content, content_type = _serialize_result(result)
        original_tokens = estimate_text_tokens(raw_content)
        inline_limit = (
            self.document_inline_token_limit
            if any(marker in tool_name.lower() for marker in _DOCUMENT_TOOL_MARKERS)
            else INLINE_TOKEN_LIMIT
        )
        if original_tokens <= inline_limit or tool_name == "read_run_evidence":
            return CompactedToolResult(result, None, original_tokens, original_tokens)
        try:
            record = self.repository.save(
                run_id=run_id,
                user_id=user_id,
                session_id=session_id,
                tool_name=tool_name,
                raw_content=raw_content,
                content_type=content_type,
            )
        except DailyAgentError:
            return CompactedToolResult(result, None, original_tokens, original_tokens)
        preview = raw_content[:PREVIEW_CHARACTER_LIMIT]
        model_content = (
            "[长工具结果已保存]\n"
            f"evidence_id: {record.evidence_id}\n"
            f"tool: {tool_name}\n"
            f"original_estimated_tokens: {original_tokens}\n"
            f"preview:\n{preview}\n"
            "预览不足以回答时，调用 read_run_evidence 按关键词或 offset 读取原始证据。"
        )
        return CompactedToolResult(
            model_content=model_content,
            evidence_id=record.evidence_id,
            original_estimated_tokens=original_tokens,
            injected_estimated_tokens=estimate_text_tokens(model_content),
        )


def build_read_evidence_tool(
    *,
    user_id: str,
    session_id: str,
    repository: EvidenceRepository | None = None,
):
    evidence_repository = repository or EvidenceRepository()

    def read_run_evidence(evidence_id: str, query: str = "", offset: int = 0) -> str:
        """读取本会话已持久化的完整工具证据；长内容可使用 query 定位或 offset 分页。"""
        record = evidence_repository.get(
            evidence_id=evidence_id,
            user_id=user_id,
            session_id=session_id,
        )
        if record is None:
            return "没有找到该证据，或它不属于当前用户和会话。"
        content = record.raw_content
        start = max(0, int(offset))
        keyword = str(query or "").strip()
        if keyword:
            matched = content.lower().find(keyword.lower())
            if matched < 0:
                return f"证据中没有找到关键词：{keyword}"
            start = max(0, matched - 400)
        chunk = content[start : start + EVIDENCE_CHUNK_CHARACTER_LIMIT]
        next_offset = start + len(chunk)
        suffix = "已到证据末尾。" if next_offset >= len(content) else f"还有内容，下一页 offset={next_offset}。"
        return (
            f"evidence_id: {record.evidence_id}\n"
            f"tool: {record.tool_name}\n"
            f"range: {start}-{next_offset}/{len(content)}\n"
            f"{chunk}\n{suffix}"
        )

    return read_run_evidence


def _serialize_result(result: Any) -> tuple[str, str]:
    if isinstance(result, str):
        return result, "text/plain"
    try:
        return json.dumps(result, ensure_ascii=False, default=str), "application/json"
    except (TypeError, ValueError):
        return str(result), "text/plain"
