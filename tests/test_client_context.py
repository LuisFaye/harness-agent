from daily_agent.client_context import build_client_context_instructions


def test_client_context_keeps_previous_assistant_option_and_drops_current_query():
    text = build_client_context_instructions(
        [
            {"role": "user", "content": "你帮我打开百度"},
            {"role": "assistant", "content": "A. 立即执行\nB. 给手动指南"},
            {"role": "user", "content": "A"},
        ],
        current_query="A",
    )

    assert "临时会话上下文" in text
    assert "助手：A. 立即执行 B. 给手动指南" in text
    assert text.count("用户：A") == 0


def test_client_context_ignores_empty_or_unknown_roles():
    text = build_client_context_instructions(
        [
            {"role": "tool", "content": "内部结果"},
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "请继续"},
        ]
    )

    assert "tool" not in text
    assert "请继续" in text


def test_client_context_drops_messages_already_sent_as_native_history():
    """前端回显通常就是历史里的最后几条，再内联一遍等于把同一段正文发两次。"""
    text = build_client_context_instructions(
        [
            {"role": "user", "content": "ds模型的最大参数是多少？"},
            {"role": "assistant", "content": "1.6T 总参数 / 49B 激活参数。"},
            {"role": "user", "content": "当前问题"},
        ],
        current_query="当前问题",
        history_messages=[
            {"role": "user", "content": "ds模型的最大参数是多少？"},
            {"role": "assistant", "content": "1.6T 总参数 / 49B 激活参数。"},
        ],
    )

    assert text == ""


def test_client_context_keeps_messages_missing_from_native_history():
    """历史里没有的消息（乐观消息、历史暂不可用）必须原样保留，不能因此丢上下文。"""
    text = build_client_context_instructions(
        [
            {"role": "user", "content": "历史里没有的问题"},
            {"role": "assistant", "content": "历史里有的回答"},
            {"role": "user", "content": "当前问题"},
        ],
        current_query="当前问题",
        history_messages=[{"role": "assistant", "content": "历史里有的回答"}],
    )

    assert "历史里没有的问题" in text
    assert "历史里有的回答" not in text


def test_client_context_matches_history_ignoring_whitespace():
    """历史正文可能带换行，回显时被压成单行；这种同一条消息也要能识别为重复。"""
    text = build_client_context_instructions(
        [{"role": "assistant", "content": "第一行 第二行"}],
        history_messages=[{"role": "assistant", "content": "第一行\n第二行"}],
    )

    assert text == ""
