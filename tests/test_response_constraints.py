from daily_agent.response_constraints import (
    enforce_response_char_limit,
    extract_response_constraints,
    extract_response_format,
    repair_format_response,
)


def test_response_constraint_can_be_overridden_and_cleared():
    messages = [
        {"role": "user", "content": "接下来回答控制在30字以内"},
        {"role": "user", "content": "以后回复最多50字"},
    ]

    assert extract_response_constraints(messages) == [
        "最终回答不得超过 50 个汉字；这是用户明确要求的持续约束，优先于默认的展开说明。"
    ]
    assert extract_response_constraints(messages, current_query="取消字数限制") == []


def test_response_char_limit_is_deterministically_enforced():
    response = "数据库索引是帮助数据库快速查找数据的数据结构，就像书的目录，不用翻遍全书。"

    limited = enforce_response_char_limit(response, 30)

    assert len(limited) <= 30
    assert limited.endswith("…")


def test_current_markdown_request_becomes_mandatory_constraint():
    constraints = extract_response_constraints([], current_query="md格式发我")

    assert extract_response_format([], current_query="md格式发我") == "markdown"
    assert any("完整、可渲染的 Markdown 正文" in item for item in constraints)
    assert any("下载 .md" in item for item in constraints)


def test_markdown_file_capability_refusal_is_replaced_with_previous_answer():
    previous = "## 冒泡排序\n\n```python\nprint('ok')\n```"

    repaired = repair_format_response(
        "目前我没有以文件形式发送 Markdown 的功能，请你复制保存。",
        "markdown",
        previous,
    )

    assert repaired == previous


def test_unnecessary_markdown_clarification_is_replaced_with_previous_answer():
    previous = "## 冒泡排序\n\n```python\nprint('ok')\n```"

    repaired = repair_format_response(
        "我需要先确认一下，你是指把刚才的内容整理成 Markdown，还是其他内容？请告诉我具体要生成什么。",
        "markdown",
        previous,
    )

    assert repaired == previous


def test_format_only_followup_always_reuses_previous_answer():
    previous = "## 冒泡排序\n\n```python\ndef bubble_sort(items):\n    return items\n```"

    repaired = repair_format_response(
        "好的，请告诉我你需要发送什么内容的 Markdown 文件？",
        "markdown",
        previous,
        current_query="md格式发我",
    )

    assert repaired == previous


def test_outer_markdown_fence_is_removed_but_inner_code_fence_is_kept():
    response = "说明文字\n\n```markdown\n# 标题\n\n```python\nprint('ok')\n```\n```\n\n请复制保存"

    repaired = repair_format_response(response, "markdown")

    assert repaired == "# 标题\n\n```python\nprint('ok')\n```"


def test_plain_markdown_question_does_not_force_markdown_output():
    assert extract_response_format([], current_query="README.md 是什么文件？") is None
