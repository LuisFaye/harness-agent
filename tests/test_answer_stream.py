from daily_agent.citations.answer_recovery import complete_answer_blocks
from daily_agent.citations.answer_stream import AgentAnswerStreamParser, finish_answer_stream


def test_repair_truncated_blocks_json_without_closing_brackets():
    raw = (
        '{"blocks":[{"markdown":"第一段","source_ids":["src-1"]},'
        '{"markdown":"第二段","source_ids":[]}]}'
    )
    blocks, mode = complete_answer_blocks(raw)
    assert mode == "structured"
    assert [item.markdown for item in blocks] == ["第一段", "第二段"]


def test_stream_parser_marks_complete_when_blocks_are_closed_but_json_is_not():
    parser = AgentAnswerStreamParser()
    chunks = [
        '{"blocks":[{"markdown":"## 标题","source_ids":[]},',
        '{"markdown":"正文段落","source_ids":["src-1"]}',
    ]
    emitted = []
    for chunk in chunks:
        emitted.extend(parser.feed(chunk))

    assert len(emitted) == 2
    stored: list = []
    text = finish_answer_stream(parser, None, emitted, stored.append)
    assert parser.complete is True
    assert text == "## 标题\n\n正文段落"
    assert stored == []


def test_recovery_falls_back_to_plain_text_for_fenced_truncated_json():
    raw = '```json\n{"blocks":[{"markdown":"第一段","source_ids":[]}]\n'
    blocks, mode = complete_answer_blocks(raw)
    assert mode == "structured"
    assert [item.markdown for item in blocks] == ["第一段"]


def test_recovery_strips_thinking_tags_and_keeps_answer():
    raw = '<thinking>让我想想</thinking>\n\n这是最终回答。'
    blocks, mode = complete_answer_blocks(raw)
    assert mode == "plain_text"
    assert [item.markdown for item in blocks] == ["这是最终回答。"]


def test_recovery_prefers_content_over_reasoning_content():
    raw = '{"reasoning_content":"思考","content":"回答内容"}'
    blocks, mode = complete_answer_blocks(raw)
    assert mode == "answer_field"
    assert [item.markdown for item in blocks] == ["回答内容"]


def test_recovery_repairs_truncated_json_with_trailing_comma():
    raw = '{"blocks":[{"markdown":"第一段","source_ids":["src-1"]},'
    blocks, mode = complete_answer_blocks(raw)
    assert mode == "structured"
    assert [item.markdown for item in blocks] == ["第一段"]
