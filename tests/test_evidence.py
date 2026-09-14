from daily_agent.evidence import EvidenceRecord, ToolResultCompactor, build_read_evidence_tool


class MemoryEvidenceRepository:
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
            estimated_tokens=2000,
        )
        self.records[record.evidence_id] = record
        return record

    def get(self, *, evidence_id, user_id, session_id):
        record = self.records.get(evidence_id)
        if not record or record.user_id != user_id or record.session_id != session_id:
            return None
        return record


def test_document_result_above_generic_limit_is_still_inlined():
    repository = MemoryEvidenceRepository()
    raw = "网页正文" * 500  # 2000 token：超过通用 800 上限，但低于文档内联上限

    compacted = ToolResultCompactor(repository).compact(
        raw,
        run_id="run-1",
        user_id="u1",
        session_id="s1",
        tool_name="web_fetch",
    )

    assert compacted.evidence_id is None
    assert compacted.model_content == raw
    assert compacted.injected_estimated_tokens == compacted.original_estimated_tokens


def test_document_result_above_document_limit_is_compacted():
    repository = MemoryEvidenceRepository()
    raw = "网页正文" * 1500  # 6000 token：超过文档内联上限

    compacted = ToolResultCompactor(repository).compact(
        raw,
        run_id="run-1",
        user_id="u1",
        session_id="s1",
        tool_name="web_fetch",
    )

    assert compacted.evidence_id == "ev-test"
    assert compacted.injected_estimated_tokens < compacted.original_estimated_tokens


def test_search_result_stays_compact_even_when_document_limit_is_high():
    repository = MemoryEvidenceRepository()
    raw = "摘要内容" * 500  # 2000 token：搜索引擎返回的是一堆摘要，不应全文内联

    compacted = ToolResultCompactor(repository).compact(
        raw,
        run_id="run-1",
        user_id="u1",
        session_id="s1",
        tool_name="mcp_web_search_bailian_web_search",
    )

    assert compacted.evidence_id == "ev-test"
    assert "长工具结果已保存" in str(compacted.model_content)


def test_document_inline_limit_is_configurable():
    repository = MemoryEvidenceRepository()
    raw = "网页正文" * 500

    compacted = ToolResultCompactor(repository, document_inline_token_limit=100).compact(
        raw,
        run_id="run-1",
        user_id="u1",
        session_id="s1",
        tool_name="web_fetch",
    )

    assert compacted.evidence_id == "ev-test"


def test_short_tool_result_stays_unchanged():
    repository = MemoryEvidenceRepository()
    result = ToolResultCompactor(repository).compact(
        "天气晴朗",
        run_id="run-1",
        user_id="u1",
        session_id="s1",
        tool_name="weather_query",
    )

    assert result.model_content == "天气晴朗"
    assert result.evidence_id is None


def test_long_tool_result_is_persisted_and_can_be_read_by_keyword():
    repository = MemoryEvidenceRepository()
    raw = "开头" + ("很长的内容" * 1000) + "关键结论：构建成功"
    compacted = ToolResultCompactor(repository).compact(
        raw,
        run_id="run-1",
        user_id="u1",
        session_id="s1",
        tool_name="project_inspector",
    )
    reader = build_read_evidence_tool(user_id="u1", session_id="s1", repository=repository)
    chunk = reader("ev-test", query="关键结论")

    assert compacted.evidence_id == "ev-test"
    assert compacted.injected_estimated_tokens < compacted.original_estimated_tokens
    assert "构建成功" in chunk
    assert reader("ev-test", query="不存在") == "证据中没有找到关键词：不存在"
