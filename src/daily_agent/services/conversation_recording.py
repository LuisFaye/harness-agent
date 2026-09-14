from __future__ import annotations

from daily_agent.errors import DailyAgentError


def record_conversation_safely(
    *,
    store_factory,
    user_id: str,
    session_id: str,
    user_input: str,
    assistant_output: str,
    run_id: str,
    suppress_user_message: bool,
    user_message_persisted: bool = False,
    attachment_ids: list[str] | None = None,
) -> None:
    """安全表单继续原任务时只补 Assistant 消息，避免重复显示同一条用户输入。"""
    try:
        store = store_factory()
        # 等待确认时可能只有交互卡片而没有正文；保留用户输入即可，
        # 不写空 Assistant 消息，也不能因此把可继续的任务变成执行失败。
        if not assistant_output.strip():
            if not suppress_user_message and not user_message_persisted and user_input.strip():
                store.add_conversation_message(
                    session_id=session_id,
                    role="user",
                    content=user_input,
                    user_id=user_id,
                    run_id=run_id,
                    attachment_ids=attachment_ids,
                )
            return
        # API 请求在创建运行时已经原子写入用户消息，结束阶段只补 Assistant，避免同一 run 重复。
        # CLI 等未经过运行仓储的入口继续使用完整轮次写入，保持原有调用兼容性。
        if suppress_user_message or user_message_persisted:
            store.add_conversation_message(
                session_id=session_id,
                role="assistant",
                content=assistant_output,
                user_id=user_id,
                run_id=run_id,
            )
            return
        store.record_conversation_turn(
            user_input=user_input,
            assistant_output=assistant_output,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            attachment_ids=attachment_ids,
        )
    except DailyAgentError:
        # 短期记忆是增强能力，写库失败不能让已经完成的 Agent 任务变成失败。
        return
