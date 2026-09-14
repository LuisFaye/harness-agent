from daily_agent.memory_support import normalize_conversation_text, normalize_memory_text


def test_conversation_text_preserves_markdown_structure():
    raw = "\r\n### 标题\r\n\r\n- 列表\r\n\r\n```python\r\n    run()\r\n```\r\n"

    assert normalize_conversation_text(raw) == "### 标题\n\n- 列表\n\n```python\n    run()\n```"


def test_long_term_memory_still_compacts_whitespace_for_deduplication():
    assert normalize_memory_text("偏好  中文\n回答") == "偏好 中文 回答"
