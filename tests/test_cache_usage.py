"""KV cache 命中的可观测性，以及"不要改写已发出的前缀"的回归保护。"""

import json

from daily_agent.evidence import EvidenceRecord, ToolResultCompactor
from daily_agent.loop_control import AgentLoopControl
from daily_agent.react_runner_helpers import format_cache_usage


class _MemoryRepository:
    """只做内存落库，避免单测依赖 MySQL。"""

    def __init__(self):
        self.records = {}

    def save(self, **kwargs):
        record = EvidenceRecord(
            evidence_id="ev-test",
            run_id=kwargs["run_id"],
            user_id=kwargs["user_id"],
            session_id=kwargs["session_id"],
            tool_name=kwargs["tool_name"],
            content_type=kwargs["content_type"],
            raw_content=kwargs["raw_content"],
            character_count=len(kwargs["raw_content"]),
            estimated_tokens=9000,
        )
        self.records[record.evidence_id] = record
        return record

    def get(self, *, evidence_id, user_id, session_id):
        record = self.records.get(evidence_id)
        if not record or record.user_id != user_id or record.session_id != session_id:
            return None
        return record


def test_cache_hit_rate_is_reported_when_provider_reports_both_sides():
    line = format_cache_usage({"cache_read_tokens": 208256, "cache_miss_tokens": 27640})

    assert "cache_read=208256" in line
    assert "cache_miss=27640" in line
    assert "cache_hit_rate=88.3%" in line


def test_cache_hit_rate_is_na_without_provider_metrics():
    assert format_cache_usage({}) == "cache_read=0 cache_miss=0 cache_hit_rate=n/a"
    assert format_cache_usage({"cache_read_tokens": None, "cache_miss_tokens": None}).endswith("n/a")


def test_cache_usage_never_raises_on_unexpected_values():
    """这行日志在结果收尾路径上，任何异常都会弄挂整轮回答。"""
    assert format_cache_usage({"cache_read_tokens": "abc", "cache_miss_tokens": True}).endswith("n/a")
    assert format_cache_usage({"cache_read_tokens": 1920, "cache_miss_tokens": True}).endswith("cache_hit_rate=100.0%")


def test_document_result_smaller_than_limit_is_sent_to_model_verbatim():
    """正文直接内联，避免"再读一次证据"多打一整轮。"""
    document = json.dumps({"url": "https://docs.example.com/a", "text": "正文" * 600}, ensure_ascii=False)
    control = AgentLoopControl(
        max_tool_calls=4,
        run_id="run-1",
        result_compactor=ToolResultCompactor(_MemoryRepository()),
    )

    output = control.tool_hook("web_fetch", lambda **_: document, {"url": "https://docs.example.com/a"})

    assert output == document


def test_search_result_is_still_compacted_so_prefix_stays_small():
    """搜索结果是摘要堆，全文内联只会把噪声写进之后每一轮的公共前缀。"""
    search_result = json.dumps(
        {"pages": [{"url": "https://example.com/a", "snippet": "摘要" * 600}]},
        ensure_ascii=False,
    )
    control = AgentLoopControl(
        max_tool_calls=4,
        run_id="run-1",
        result_compactor=ToolResultCompactor(_MemoryRepository()),
    )

    output = control.tool_hook(
        "mcp_web_search_bailian_web_search",
        lambda **_: search_result,
        {"query": "agno 上下文"},
    )

    assert "长工具结果已保存" in output
    assert "ev-test" in output
