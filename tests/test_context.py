import daily_agent.context as context_module
from daily_agent.context import AgentContext, MySQLContextBuilder
from daily_agent.memory import MemoryRecord, Preference, TaskHistory, ToolCallLog


class FakeStore:
    def list_preferences(self, user_id):
        return [
            Preference(key="language", value="zh"),
            Preference(key="tone", value="direct"),
        ]

    def recent_memories(self, user_id, limit):
        return ["用户希望回答尽量简洁。"]

    def list_conversation_messages(self, user_id, session_id, limit=None):
        return [
            type("Msg", (), {"role": "assistant", "content": "项目结构已梳理"})(),
            type("Msg", (), {"role": "user", "content": "梳理项目结构"})(),
        ]

    def get_conversation_summary(self, user_id, session_id):
        return None

    def list_task_history(self, user_id, limit):
        return [
            TaskHistory(
                id=1,
                task_type="ask",
                user_input="梳理项目结构",
                output_summary="项目结构已梳理",
                status="success",
                error_message=None,
            )
        ]

    def list_tool_call_logs(self, user_id, limit):
        return [
            ToolCallLog(
                id=1,
                tool_name="project_inspector",
                input_json="{}",
                output_summary="项目结构摘要",
                status="success",
                error_message=None,
                duration_ms=100,
            )
        ]


def test_context_builder_compresses_older_conversation():
    class BiggerStore(FakeStore):
        def list_conversation_messages(self, user_id, session_id, limit=None):
            messages = []
            for idx in range(8, 0, -1):
                role = "assistant" if idx % 2 == 0 else "user"
                content = f"第{idx}条{role}消息"
                messages.append(type("Msg", (), {"role": role, "content": content})())
            return messages

    context = MySQLContextBuilder(store=BiggerStore()).build()
    text = context.to_instructions()

    assert "会话摘要" in text
    assert "最近会话" in text
    assert "第1条user消息" in text
    assert "第8条assistant消息" in text


def test_context_to_instructions_applies_token_budget(monkeypatch):
    monkeypatch.setattr(context_module, "estimate_text_tokens", lambda text: len(text))

    context = AgentContext(
        user_id="u1",
        preferences=["偏好信息 1"],
        memories=["长期记忆 1"],
        conversation_summary=["会话摘要 1"],
        recent_conversation=["最近会话 1"],
        recent_tasks=["最近任务 1"],
        recent_tool_calls=["最近工具 1"],
    )

    text = context.to_instructions(token_budget=80)

    assert "用户偏好" in text
    assert "最近工具结果" not in text
    assert "上下文已截断" in text or len(text) <= 80


def test_context_builder_uses_query_to_recall_relevant_memory():
    class SearchableStore(FakeStore):
        def list_memories(self, user_id, limit):
            return [
                MemoryRecord(id=2, memory_text="我主要使用 Python 3.11", source="auto"),
                MemoryRecord(id=1, memory_text="我住在成都", source="manual"),
            ]

    text = MySQLContextBuilder(store=SearchableStore()).build(query="我用哪个 Python 版本？").to_instructions()

    assert "Python 3.11" in text
    assert "我住在成都" not in text


def test_context_builder_injects_self_identity_across_sessions():
    class IdentityStore(FakeStore):
        def list_preferences(self, user_id):
            return [Preference(key="self_identity", value="父王")]

        def list_conversation_messages(self, user_id, session_id, limit=None):
            return []

    text = MySQLContextBuilder(store=IdentityStore()).build(
        user_id="u1",
        session_id="new-session",
        query="我是谁",
    ).to_instructions()

    assert "用户明确自述的身份: 父王" in text


def test_context_builder_inherits_persistent_response_length_constraint():
    class ConstraintStore(FakeStore):
        def list_conversation_messages(self, user_id, session_id, limit=None):
            return [
                type("Msg", (), {"role": "user", "content": "接下来回答控制在30字以内"})(),
                type("Msg", (), {"role": "assistant", "content": "好的"})(),
            ]

    context = MySQLContextBuilder(store=ConstraintStore()).build(query="介绍一下成都")
    text = context.to_instructions()

    assert context.active_constraints == ["最终回答不得超过 30 个汉字；这是用户明确要求的持续约束，优先于默认的展开说明。"]
    assert "当前会话约束" in text
    assert "30 个汉字" in text


def test_context_builder_applies_current_markdown_format_constraint():
    context = MySQLContextBuilder(store=FakeStore()).build(query="md格式发我")

    assert any("Markdown 正文" in item for item in context.active_constraints)
    assert "下载 .md" in context.to_instructions()


def test_context_filters_removed_domain_history(monkeypatch):
    monkeypatch.setattr(context_module, "list_enabled_tool_names", lambda: {"project_inspector"})

    class LegacyDomainStore(FakeStore):
        def recent_memories(self, user_id, limit):
            return ["用户希望回答简洁。", "用户关注 BTC 合约策略。"]

        def list_conversation_messages(self, user_id, session_id, limit=None):
            return [
                type("Msg", (), {"role": "assistant", "content": "可以查询比特币价格"})(),
                type("Msg", (), {"role": "user", "content": "介绍你能做什么"})(),
            ]

        def list_task_history(self, user_id, limit):
            return [
                TaskHistory(
                    id=1,
                    task_type="ask",
                    user_input="介绍你能做什么",
                    output_summary="可以查询 BTC 实时价格",
                    status="success",
                    error_message=None,
                ),
                TaskHistory(
                    id=2,
                    task_type="ask",
                    user_input="梳理项目结构",
                    output_summary="项目结构已梳理",
                    status="success",
                    error_message=None,
                ),
            ]

        def list_tool_call_logs(self, user_id, limit):
            return [
                ToolCallLog(
                    id=1,
                    tool_name="get_bitcoin_price",
                    input_json="{}",
                    output_summary="BTC 价格",
                    status="success",
                    error_message=None,
                    duration_ms=100,
                ),
                ToolCallLog(
                    id=2,
                    tool_name="project_inspector",
                    input_json="{}",
                    output_summary="项目结构摘要",
                    status="success",
                    error_message=None,
                    duration_ms=100,
                ),
            ]

    text = MySQLContextBuilder(store=LegacyDomainStore()).build().to_instructions()

    assert "BTC" not in text
    assert "比特币" not in text
    assert "get_bitcoin_price" not in text
    assert "项目结构已梳理" not in text
    assert "项目结构摘要" not in text
    assert "project_inspector" not in text
