"""长期记忆的轻量相关性排序。

当前数据量很小，先在最近一批候选上做本地词项排序，避免为了召回几条个人记忆
引入向量数据库。后续数据量增长时，可以保持本模块接口不变再替换为向量检索。
"""

from __future__ import annotations

import re
from typing import Iterable

from daily_agent.memory_models import MemoryRecord

_LATIN_TOKEN_RE = re.compile(r"[a-zA-Z0-9_./-]{2,}")
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
_STOP_TERMS = {
    "什么",
    "怎么",
    "可以",
    "这个",
    "那个",
    "用户",
    "记住",
    "告诉",
    "知道",
}


def select_relevant_memories(
    query: str,
    records: Iterable[MemoryRecord],
    *,
    limit: int = 5,
) -> list[str]:
    candidates = list(records)
    if limit <= 0 or not candidates:
        return []
    query_terms = _terms(query)
    if not query_terms:
        return [item.memory_text for item in candidates[:limit]]

    scored: list[tuple[float, int, str]] = []
    query_compact = _compact(query)
    for index, record in enumerate(candidates):
        text = str(record.memory_text or "").strip()
        if not text:
            continue
        memory_terms = _terms(text)
        overlap = query_terms.intersection(memory_terms)
        score = float(sum(max(1, len(term)) for term in overlap))
        memory_compact = _compact(text)
        if query_compact and (query_compact in memory_compact or memory_compact in query_compact):
            score += 10.0
        if score > 0:
            # 候选按时间倒序读取；相同相关性时保留较新的记忆。
            scored.append((score, -index, text))
    scored.sort(reverse=True)
    return [text for _, _, text in scored[:limit]]


def _terms(value: str) -> set[str]:
    text = str(value or "").lower()
    terms = {token for token in _LATIN_TOKEN_RE.findall(text) if token not in _STOP_TERMS}
    for segment in _CHINESE_RE.findall(text):
        terms.update(segment[index:index + 2] for index in range(max(0, len(segment) - 1)))
    return {term for term in terms if term and term not in _STOP_TERMS}


def _compact(value: str) -> str:
    return "".join(str(value or "").lower().split())
