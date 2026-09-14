"""把 Agent 同次生成的段落来源关系组装成可信引用协议。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import logging
import time

from daily_agent.citations.markdown_blocks import AnswerBlock, parse_answer_blocks
from daily_agent.citations.models import (
    AgentAnswerBlock,
    CitationBundle,
    CitationChunk,
    CitationSource,
    CitationSupport,
    content_hash,
)
from daily_agent.citations.registry import WebCitationRegistry
from daily_agent.citations.web_fetch import best_passage, fetch_page_text


logger = logging.getLogger(__name__)
MAX_CITATION_SECONDS = 10.0
MAX_FETCH_SOURCES = 5
MAX_SOURCES_PER_BLOCK = 3


def compose_agent_citations(
    answer_text: str,
    agent_blocks: list[AgentAnswerBlock],
    registry: WebCitationRegistry,
    *,
    fetch_pages: bool,
    prefetched_pages: dict[str, str] | None = None,
) -> CitationBundle:
    """只接受 Agent 已经随段落生成的 source_id，不再调用模型做事后对齐。

    ``source_id`` 仍需经过服务端注册表校验。流式阶段可以传入回答生成期间已经
    后台读取完成的网页，把摘要证据升级为正文 passage；尚未完成或读取失败时
    保持摘要级引用，最终组装本身不等待网络。
    """
    unavailable = CitationBundle.unavailable(answer_text)
    if not registry.has_sources or not agent_blocks:
        return unavailable
    try:
        sources, chunks, snippet_chunks = registry.snapshot()
        answer_blocks = parse_answer_blocks(answer_text)
        mappings = _map_agent_blocks(agent_blocks, answer_blocks, sources)
        if not mappings:
            return unavailable
        fetched_pages = dict(prefetched_pages or {})
        if fetch_pages:
            fetched_pages = _fetch_selected_pages(
                mappings,
                sources,
                deadline=time.monotonic() + MAX_CITATION_SECONDS,
            )
        return _build_bundle(
            answer_text=answer_text,
            answer_blocks=answer_blocks,
            sources=sources,
            chunks=chunks,
            snippet_chunks=snippet_chunks,
            mappings=mappings,
            fetched_pages=fetched_pages,
        )
    except Exception:
        logger.exception("agent citation composition failed: run_id=%s", registry.run_id)
        return unavailable


def reanchor_bundle(answer_text: str, bundle: CitationBundle | None) -> CitationBundle | None:
    """回答被长度或格式约束调整后，只保留仍能在正文中找到的引用关系。"""
    if bundle is None:
        return None
    if bundle.status == "unavailable":
        return CitationBundle.unavailable(answer_text)
    blocks = parse_answer_blocks(answer_text)
    block_by_quote = {item.text: item.block_id for item in blocks}
    supports: list[CitationSupport] = []
    for support in bundle.supports:
        block_id = block_by_quote.get(support.answer_quote)
        if not block_id:
            continue
        supports.append(support.model_copy(update={"block_id": block_id}))
    if not supports:
        return CitationBundle.unavailable(answer_text)
    used_chunk_ids = {item for support in supports for item in support.chunk_ids}
    chunks = [item for item in bundle.chunks if item.chunk_id in used_chunk_ids]
    used_source_ids = {item.source_id for item in chunks}
    sources = [item for item in bundle.sources if item.source_id in used_source_ids]
    status = "complete" if all(item.support_status == "supported" for item in supports) else "partial"
    return CitationBundle(
        status=status,
        answer_hash=content_hash(answer_text),
        sources=sources,
        chunks=chunks,
        supports=supports,
    )


def _map_agent_blocks(
    agent_blocks: list[AgentAnswerBlock],
    answer_blocks: list[AnswerBlock],
    sources: list[CitationSource],
) -> list[tuple[str, list[str]]]:
    """按生成顺序把结构化块映射到最终 Markdown 解析出的稳定块 ID。"""
    valid_source_ids = {item.source_id for item in sources}
    mappings: list[tuple[str, list[str]]] = []
    answer_cursor = 0
    for agent_block in agent_blocks:
        selected_ids: list[str] = []
        for source_id in agent_block.source_ids:
            normalized = str(source_id or "")
            if normalized in valid_source_ids and normalized not in selected_ids:
                selected_ids.append(normalized)
            if len(selected_ids) >= MAX_SOURCES_PER_BLOCK:
                break
        local_blocks = parse_answer_blocks(agent_block.markdown)
        for local_block in local_blocks:
            matched_index = _find_block_index(answer_blocks, local_block.text, start=answer_cursor)
            if matched_index is None:
                continue
            answer_cursor = matched_index + 1
            if selected_ids:
                mappings.append((answer_blocks[matched_index].block_id, list(selected_ids)))
    return mappings


def _find_block_index(blocks: list[AnswerBlock], text: str, *, start: int) -> int | None:
    for index in range(start, len(blocks)):
        if blocks[index].text == text:
            return index
    return None


def _fetch_selected_pages(
    mappings: list[tuple[str, list[str]]],
    sources: list[CitationSource],
    *,
    deadline: float,
) -> dict[str, str]:
    source_by_id = {item.source_id: item for item in sources}
    selected_ids: list[str] = []
    for _, source_ids in mappings:
        for source_id in source_ids:
            if source_id not in selected_ids:
                selected_ids.append(source_id)
            if len(selected_ids) >= MAX_FETCH_SOURCES:
                break
        if len(selected_ids) >= MAX_FETCH_SOURCES:
            break
    if not selected_ids:
        return {}
    pages: dict[str, str] = {}
    # 被引用网页之间相互独立，并发读取可以把多个站点的网络等待限制在同一总时限内。
    with ThreadPoolExecutor(max_workers=min(3, len(selected_ids))) as executor:
        futures = {
            executor.submit(fetch_page_text, source_by_id[source_id].url, deadline=deadline): source_id
            for source_id in selected_ids
            if source_id in source_by_id
        }
        for future in as_completed(futures):
            if time.monotonic() >= deadline:
                break
            try:
                page_text = future.result()
            except Exception:
                page_text = None
            if page_text:
                pages[futures[future]] = page_text
    return pages


def _build_bundle(
    *,
    answer_text: str,
    answer_blocks: list[AnswerBlock],
    sources: list[CitationSource],
    chunks: list[CitationChunk],
    snippet_chunks: dict[str, str],
    mappings: list[tuple[str, list[str]]],
    fetched_pages: dict[str, str],
) -> CitationBundle:
    block_by_id = {item.block_id: item for item in answer_blocks}
    source_by_id = {item.source_id: item for item in sources}
    chunk_by_id = {item.chunk_id: item for item in chunks}
    used_chunks: dict[str, CitationChunk] = {}
    used_sources: dict[str, CitationSource] = {}
    supports: list[CitationSupport] = []
    for block_id, source_ids in mappings:
        answer_block = block_by_id.get(block_id)
        if not answer_block:
            continue
        support_chunk_ids: list[str] = []
        for source_id in source_ids:
            source = source_by_id.get(source_id)
            if not source:
                continue
            snippet_chunk = chunk_by_id.get(snippet_chunks.get(source_id, ""))
            document_text = fetched_pages.get(source_id, "")
            passage = (
                best_passage(document_text, answer_block.text, snippet_chunk.text if snippet_chunk else "")
                if document_text
                else None
            )
            if passage:
                passage_text, start, end = passage
                chunk_id = _stable_id("chk", f"{source_id}|passage|{passage_text}")
                chunk = CitationChunk(
                    chunk_id=chunk_id,
                    source_id=source_id,
                    text=passage_text,
                    locator={"type": "character_range", "start": start, "end": end, "unit": "unicode_codepoint"},
                    content_hash=content_hash(passage_text),
                )
                used_sources[source_id] = source.model_copy(
                    update={"content_level": "passage", "content_hash": content_hash(document_text)}
                )
            elif snippet_chunk:
                chunk = snippet_chunk
                used_sources[source_id] = source
            else:
                continue
            used_chunks[chunk.chunk_id] = chunk
            support_chunk_ids.append(chunk.chunk_id)
        if not support_chunk_ids:
            continue
        fully_supported = all(
            used_sources[used_chunks[chunk_id].source_id].content_level == "passage"
            for chunk_id in support_chunk_ids
        )
        supports.append(
            CitationSupport(
                support_id=_stable_id("sup", f"{block_id}|{'|'.join(support_chunk_ids)}"),
                block_id=block_id,
                answer_quote=answer_block.text,
                chunk_ids=support_chunk_ids,
                support_status="supported" if fully_supported else "partial",
                origin="agent_generated",
            )
        )
    if not supports:
        return CitationBundle.unavailable(answer_text)
    status = "complete" if all(item.support_status == "supported" for item in supports) else "partial"
    return CitationBundle(
        status=status,
        answer_hash=content_hash(answer_text),
        sources=list(used_sources.values()),
        chunks=list(used_chunks.values()),
        supports=supports,
    )


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"
