from daily_agent.memory_models import MemoryRecord
from daily_agent.memory_retrieval import select_relevant_memories


def test_retrieval_prefers_memories_related_to_current_query():
    records = [
        MemoryRecord(id=3, memory_text="我常用的编辑器是 VS Code", source="auto"),
        MemoryRecord(id=2, memory_text="我主要使用 Python 3.11", source="auto"),
        MemoryRecord(id=1, memory_text="我住在成都", source="manual"),
    ]

    selected = select_relevant_memories("我平时用哪个 Python 版本？", records, limit=2)

    assert selected == ["我主要使用 Python 3.11"]


def test_retrieval_does_not_inject_unrelated_long_term_memory():
    records = [MemoryRecord(id=1, memory_text="我住在成都", source="manual")]

    assert select_relevant_memories("帮我解释依赖注入", records) == []
