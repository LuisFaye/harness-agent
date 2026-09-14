"""KV cache 前缀布局回归：越靠前的内容必须越稳定。

覆盖三件事：
1. 用户偏好留在历史之前，长期记忆/摘要放到历史之后（记忆写入不再让整段历史失效）；
2. 日期等运行时事实不再进 system，只随本轮尾部上下文发送；
3. 引用来源注册表不再重复内联正文摘要。
"""

import daily_agent.agent as agent_module
from daily_agent.agent import _build_input_messages, _build_runtime_context_instructions, build_prompt_snapshot
from daily_agent.citations.registry import BAILIAN_WEB_SEARCH_TOOL, WebCitationRegistry
from daily_agent.context import MySQLContextBuilder
from daily_agent.memory import Preference
from daily_agent.request_preparation import preparation_scope

HISTORY = [
    {"role": "user", "content": "上一轮问题"},
    {"role": "assistant", "content": "上一轮回答"},
]


class _Store:
    """最小内存 store：只提供 prompt 上下文需要的四个读取入口。"""

    def __init__(self, *, memories=None, summary=None, summary_count=0):
        self.memories = list(memories or [])
        self.summary = summary
        self.summary_count = summary_count

    def list_preferences(self, user_id):
        return [Preference(key="self_identity", value="父王")]

    def recent_memories(self, user_id, limit):
        return self.memories[:limit]

    def list_conversation_messages(self, user_id, session_id, limit=None):
        items = [
            type("Msg", (), {"role": "user", "content": "上一轮问题", "run_id": ""})(),
            type("Msg", (), {"role": "assistant", "content": "上一轮回答", "run_id": ""})(),
        ]
        return list(reversed(items)) if limit else items

    def get_conversation_summary(self, user_id, session_id):
        if self.summary is None:
            return None
        return type(
            "Summary",
            (),
            {"summary_text": self.summary, "summarized_message_count": self.summary_count},
        )()


def _context(store):
    return MySQLContextBuilder(store=store).build_prompt_message_context(
        user_id="u1", session_id="s1"
    )


def test_preferences_stay_before_history_and_memories_move_after_it():
    context = _context(_Store(memories=["叫我大哥"]))

    assert "父王" in context.session_context
    assert "叫我大哥" not in context.session_context
    assert "叫我大哥" in context.volatile_context
    assert "父王" not in context.volatile_context


def test_new_memory_does_not_change_the_stable_head_block():
    """记忆写入只影响尾部；session_context 必须逐字不变，否则历史前缀全部失效。"""
    before = _context(_Store(memories=["叫我大哥"]))
    after = _context(_Store(memories=["叫我大哥", "用户住在成都"]))

    assert before.session_context == after.session_context
    assert before.volatile_context != after.volatile_context


def test_frozen_summary_is_sent_in_the_volatile_block():
    context = _context(_Store(memories=[], summary="更早的会话摘要", summary_count=2))

    assert "更早的会话摘要" in context.volatile_context
    assert "更早的会话摘要" not in context.session_context


def test_runtime_facts_travel_with_the_current_turn_not_the_system_prompt():
    instructions = agent_module._build_instructions(
        query="今天几号", plan=None, enable_tools=True, enabled_skills=[], tool_names=[]
    )

    assert not any("当前日期：" in item for item in instructions)


def test_message_order_is_stable_then_volatile():
    messages = _build_input_messages(
        session_context="用户上下文：\n用户偏好：\n- 身份: 父王",
        volatile_context="用户上下文：\n长期记忆：\n- 叫我大哥",
        history_messages=list(HISTORY),
        runtime_instructions=_build_runtime_context_instructions(),
        extra_context_instructions="临时会话上下文：上一轮让用户选择 A/B",
    )

    roles = [item["role"] for item in messages]
    assert roles == ["user", "user", "assistant", "user", "user"]
    contents = [str(item["content"]) for item in messages]
    assert contents[0].startswith("<session_context>")
    assert "父王" in contents[0]
    assert contents[1] == "上一轮问题"
    assert contents[3].startswith("<session_memory>")
    assert "叫我大哥" in contents[3]
    assert contents[4].startswith("<current_turn_context>")
    # 运行时事实与临时上下文都在最后一轮，历史之前没有任何易变内容。
    assert "当前日期：" in contents[4]
    assert "让用户选择 A/B" in contents[4]


def test_volatile_block_is_omitted_when_there_is_nothing_volatile():
    messages = _build_input_messages(
        session_context="用户上下文：\n用户偏好：\n- 身份: 父王",
        history_messages=list(HISTORY),
        runtime_instructions="运行时上下文：\n- 当前日期：2026-05-29",
    )

    assert [item["role"] for item in messages] == ["user", "user", "assistant", "user"]
    assert all(not str(item["content"]).startswith("<session_memory>") for item in messages)


def test_client_context_dedupe_reuses_the_prompt_context_read(monkeypatch):
    """回显去重和 prompt 构建必须共用一次上下文读取，不能为了去重多读一遍库。"""
    reads = {"count": 0}

    class CountingStore(_Store):
        def list_conversation_messages(self, user_id, session_id, limit=None):
            reads["count"] += 1
            return super().list_conversation_messages(user_id, session_id, limit=limit)

    store = CountingStore(memories=["叫我大哥"])
    monkeypatch.setattr(agent_module, "MySQLContextBuilder", lambda: MySQLContextBuilder(store=store))

    @preparation_scope
    def within_one_ask():
        # 先为“客户端回显去重”读一次历史，再构建 prompt：第二次必须命中复用缓存。
        context = MySQLContextBuilder(store=store).build_prompt_message_context(
            user_id="u1", session_id="s1"
        )
        snapshot = build_prompt_snapshot(
            query="第二轮问题", user_id="u1", session_id="s1", enable_tools=False
        )
        return context, snapshot

    context, snapshot = within_one_ask()

    assert reads["count"] == 1
    assert "叫我大哥" in context.volatile_context
    assert any("<session_memory>" in str(item.get("content")) for item in snapshot.input_messages)


def test_citation_registry_does_not_repeat_the_snippet_preview():
    registry = WebCitationRegistry("run-1")
    snippet = "激活参数指每次前向只参与计算的参数子集。" * 8
    source_ids = registry.capture(
        BAILIAN_WEB_SEARCH_TOOL,
        {"pages": [{"url": "https://example.com/a", "title": "激活参数", "snippet": snippet}]},
    )

    rendered = registry.render_model_context(source_ids)

    assert source_ids[0] in rendered
    assert "https://example.com/a" in rendered
    assert "激活参数" in rendered
    # 正文摘要已经在工具结果里发送过，注册表只回传 id/标题/url。
    assert "每次前向只参与计算的参数子集" not in rendered
