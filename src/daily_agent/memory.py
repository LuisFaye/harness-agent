"""MySQL 持久化：偏好、长期记忆、会话、任务与工具日志。

表结构见 sql/mysql_memory.sql。conversation_* 给模型续聊，task_history 用于排查；
ask 成功后由 refresh_conversation_summary 刷新窗口外摘要。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from daily_agent.conversation_summary import compress_conversation_history
from daily_agent.conversation_session_store import (
    list_conversation_sessions as _list_conversation_sessions,
    literal_like_pattern as _literal_like_pattern,
)
from daily_agent.config import Settings, load_settings
from daily_agent.errors import MissingConfigError
from daily_agent.memory_models import (
    ConversationCleanupResult,
    ConversationMessage,
    ConversationSessionRecord,
    ConversationSummary,
    MemoryDeduplicationResult,
    MemoryRecord,
    Preference,
    ScheduledJob,
    ScheduledJobRun,
    TaskHistory,
    ToolCallLog,
)
from daily_agent.mysql_pool import borrow_connection
from daily_agent.memory_support import (
    DEFAULT_CONVERSATION_COMPACTION_TOKEN_THRESHOLD,
    DEFAULT_CONVERSATION_EPOCH_TAIL_TURNS,
    DEFAULT_CONVERSATION_TURN_LIMIT,
    DEFAULT_SESSION_ID,
    DEFAULT_USER_ID,
    SCHEMA_PATH,
    build_tool_log_input_payload as _build_tool_log_input_payload,
    load_schema_statements,
    normalize_attachment_ids as _normalize_attachment_ids,
    normalize_conversation_text,
    parse_attachment_ids as _parse_attachment_ids,
    memory_record_from_row as _memory_record_from_row,
    normalize_memory_text,
    normalize_session_id,
    scheduled_job_from_row as _scheduled_job_from_row,
    scheduled_job_run_from_row as _scheduled_job_run_from_row,
    utc_now_iso,
)


class MySQLMemoryStore:
    """SB Agent 的 MySQL 访问层；所有记忆相关 CLI 命令最终都走这里。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        if not self.settings.mysql_user:
            raise MissingConfigError("缺少 SB_AGENT_MYSQL_USER（兼容 DAILY_AGENT_MYSQL_USER），无法连接 MySQL 记忆库。")

    def init_schema(self) -> None:
        database = self.settings.mysql_database
        with self._connect(database=None) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            connection.commit()

        with self._connect() as connection:
            with connection.cursor() as cursor:
                for statement in load_schema_statements():
                    try:
                        cursor.execute(statement)
                    except Exception as exc:
                        if not exc.args or exc.args[0] not in {1060, 1061}:
                            raise
            connection.commit()
    def set_preference(self, key: str, value: str, user_id: str = DEFAULT_USER_ID) -> None:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("偏好 key 不能为空。")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO user_preferences (user_id, pref_key, pref_value)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE pref_value = VALUES(pref_value)
                    """,
                    (user_id, normalized_key, value),
                )
            connection.commit()

    def delete_preference(self, key: str, user_id: str = DEFAULT_USER_ID) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM user_preferences WHERE user_id = %s AND pref_key = %s",
                    (user_id, key),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
        return deleted

    def list_preferences(self, user_id: str = DEFAULT_USER_ID) -> list[Preference]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pref_key, pref_value, updated_at
                    FROM user_preferences
                    WHERE user_id = %s
                    ORDER BY pref_key
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()
        return [
            Preference(key=row["pref_key"], value=row["pref_value"], updated_at=row["updated_at"])
            for row in rows
        ]

    def add_memory(self, text: str, user_id: str = DEFAULT_USER_ID, source: str = "manual",
                   expires_at: datetime | None = None) -> MemoryRecord:
        normalized_text = normalize_memory_text(text)
        normalized_source = source.strip() or "manual"
        if not normalized_text:
            raise ValueError("记忆内容不能为空。")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                # 长期记忆会反复注入上下文；按用户、来源、正文精确去重，避免重复内容挤占空间。
                cursor.execute(
                    """
                    SELECT id, memory_text, source, created_at, expires_at
                    FROM conversation_memories
                    WHERE user_id = %s AND source = %s AND memory_text = %s
                      AND (expires_at IS NULL OR expires_at > UTC_TIMESTAMP())
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                    """,
                    (user_id, normalized_source, normalized_text),
                )
                row = cursor.fetchone()
                if row:
                    return _memory_record_from_row(row)

                cursor.execute(
                    """
                    INSERT INTO conversation_memories (user_id, memory_text, source, expires_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (user_id, normalized_text, normalized_source, expires_at),
                )
                memory_id = int(cursor.lastrowid)
            connection.commit()
        return MemoryRecord(id=memory_id, memory_text=normalized_text, source=normalized_source,
                            expires_at=expires_at)

    def add_conversation_message(
        self,
        session_id: str,
        role: str,
        content: str,
        user_id: str = DEFAULT_USER_ID,
        run_id: str | None = None,
        attachment_ids: list[str] | None = None,
    ) -> ConversationMessage:
        normalized_session_id = normalize_session_id(session_id)
        normalized_role = role.strip().lower()
        normalized_content = normalize_conversation_text(content)
        if not normalized_session_id:
            raise ValueError("会话 ID 不能为空。")
        if normalized_role not in {"user", "assistant", "system"}:
            raise ValueError("会话消息角色只能是 user、assistant 或 system。")
        if not normalized_content:
            raise ValueError("会话消息内容不能为空。")
        normalized_attachment_ids = _normalize_attachment_ids(attachment_ids)
        if normalized_attachment_ids and normalized_role != "user":
            raise ValueError("只有用户消息可以附带附件。")

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO conversation_messages (user_id, session_id, role, content, run_id, attachment_ids)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        normalized_session_id,
                        normalized_role,
                        normalized_content,
                        run_id,
                        json.dumps(normalized_attachment_ids, ensure_ascii=False)
                        if normalized_attachment_ids
                        else None,
                    ),
                )
                message_id = int(cursor.lastrowid)
            connection.commit()
        return ConversationMessage(
            id=message_id,
            session_id=normalized_session_id,
            role=normalized_role,
            content=normalized_content,
            run_id=run_id,
            attachment_ids=normalized_attachment_ids,
        )

    def delete_trailing_assistant_messages(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> int:
        """删除当前会话最后一轮用户提问之后的旧 Assistant 回复，用于重试或重新生成时原地覆盖。"""
        normalized_session_id = normalize_session_id(session_id)
        if not normalized_session_id:
            return 0
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, role
                    FROM conversation_messages
                    WHERE user_id = %s AND session_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT 30
                    """,
                    (user_id, normalized_session_id),
                )
                rows = cursor.fetchall()
                trailing_ids: list[int] = []
                for row in rows:
                    if row["role"] == "assistant":
                        trailing_ids.append(int(row["id"]))
                    else:
                        break
                if not trailing_ids:
                    return 0
                placeholders = ", ".join(["%s"] * len(trailing_ids))
                cursor.execute(
                    f"""
                    DELETE FROM conversation_messages
                    WHERE user_id = %s AND session_id = %s AND id IN ({placeholders})
                    """,
                    (user_id, normalized_session_id, *trailing_ids),
                )
                deleted = int(cursor.rowcount or 0)
            connection.commit()
        return deleted

    def save_conversation_summary(
        self,
        user_id: str,
        session_id: str,
        summary_text: str,
        summarized_message_count: int,
    ) -> ConversationSummary:
        normalized_session_id = normalize_session_id(session_id)
        normalized_summary = summary_text.strip()
        if not normalized_summary:
            raise ValueError("会话摘要不能为空。")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO conversation_session_summaries
                        (user_id, session_id, summary_text, summarized_message_count)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        summary_text = VALUES(summary_text),
                        summarized_message_count = VALUES(summarized_message_count)
                    """,
                    (user_id, normalized_session_id, normalized_summary, max(0, int(summarized_message_count))),
                )
            connection.commit()
        return ConversationSummary(
            user_id=user_id,
            session_id=normalized_session_id,
            summary_text=normalized_summary,
            summarized_message_count=max(0, int(summarized_message_count)),
        )

    def get_conversation_summary(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> ConversationSummary | None:
        normalized_session_id = normalize_session_id(session_id)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT user_id, session_id, summary_text, summarized_message_count, updated_at
                    FROM conversation_session_summaries
                    WHERE user_id = %s AND session_id = %s
                    LIMIT 1
                    """,
                    (user_id, normalized_session_id),
                )
                row = cursor.fetchone()
        if not row:
            return None
        return ConversationSummary(
            user_id=row["user_id"],
            session_id=row["session_id"],
            summary_text=row["summary_text"],
            summarized_message_count=int(row["summarized_message_count"]),
            updated_at=row["updated_at"],
        )

    def delete_conversation_summary(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> bool:
        normalized_session_id = normalize_session_id(session_id)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM conversation_session_summaries
                    WHERE user_id = %s AND session_id = %s
                    """,
                    (user_id, normalized_session_id),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
        return deleted

    def get_conversation_title(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> str | None:
        """读取已经持久化的语义标题；没有生成过时返回 None。"""
        normalized_session_id = normalize_session_id(session_id)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT title
                    FROM conversation_session_titles
                    WHERE user_id = %s AND session_id = %s
                    LIMIT 1
                    """,
                    (user_id, normalized_session_id),
                )
                row = cursor.fetchone()
        title = str((row or {}).get("title") or "").strip()
        return title or None

    def save_conversation_title(
        self,
        title: str,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        source: str = "model",
    ) -> str:
        """首次写入会话语义标题；已有非空标题不会被后续问答覆盖。"""
        normalized_session_id = normalize_session_id(session_id)
        normalized_title = " ".join(str(title or "").split()).strip()
        if not normalized_title:
            raise ValueError("会话标题不能为空。")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO conversation_session_titles (user_id, session_id, title, source)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        title = IF(title = '', VALUES(title), title),
                        source = IF(title = '', VALUES(source), source)
                    """,
                    (user_id, normalized_session_id, normalized_title, source.strip() or "model"),
                )
            connection.commit()
        return normalized_title

    def refresh_conversation_summary(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        keep_messages: int = DEFAULT_CONVERSATION_EPOCH_TAIL_TURNS,
        token_threshold: int = DEFAULT_CONVERSATION_COMPACTION_TOKEN_THRESHOLD,
    ) -> ConversationSummary | None:
        """达到阈值时推进一次摘要高水位，在两次压缩之间保持 cache epoch 不变。

        token_threshold 使用保守字符数估算未摘要消息的 Token；只有尾部超过阈值才
        重新生成摘要。keep_messages 表示压缩后至少保留的最近完整轮数。相比每轮都把
        滑动窗口外内容重新摘要，这种方式让 system、冻结摘要和后续原生消息形成可追加
        的公共前缀；压缩发生时允许一次缓存失配，然后进入新的稳定 epoch。
        """
        all_messages = list(reversed(self.list_conversation_messages(user_id=user_id, session_id=session_id)))
        stored_summary = self.get_conversation_summary(user_id=user_id, session_id=session_id)
        summarized_count = max(0, int(stored_summary.summarized_message_count)) if stored_summary else 0
        if summarized_count > len(all_messages):
            # 历史清理或外部数据修复可能使旧高水位失效。此时删除失配摘要，从完整剩余
            # 消息重新建立 epoch，不能让计数越界后错误跳过仍然存在的对话。
            self.delete_conversation_summary(user_id=user_id, session_id=session_id)
            stored_summary = None
            summarized_count = 0

        unsummarized_messages = all_messages[summarized_count:]
        estimated_tokens = sum(len(str(item.content or "")) + 8 for item in unsummarized_messages)
        if estimated_tokens <= max(1, int(token_threshold)):
            return stored_summary

        recent_window = max(0, int(keep_messages)) * 2
        next_summarized_count = len(all_messages) - recent_window if recent_window else len(all_messages)
        next_summarized_count = max(summarized_count, next_summarized_count)
        if next_summarized_count <= summarized_count:
            return stored_summary

        summary_lines = compress_conversation_history(all_messages[:next_summarized_count])
        summary_text = "\n".join(summary_lines).strip()
        if not summary_text:
            return stored_summary
        return self.save_conversation_summary(
            user_id=user_id,
            session_id=session_id,
            summary_text=summary_text,
            summarized_message_count=next_summarized_count,
        )

    def record_conversation_turn(
        self,
        user_input: str,
        assistant_output: str,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        run_id: str | None = None,
        attachment_ids: list[str] | None = None,
    ) -> list[ConversationMessage]:
        # 短期记忆不应该和 task_history 混用。
        # task_history 更像“任务日志”，而 conversation_messages 则保留最近几轮对话原文，
        # 这样上下文组装时可以直接拼给模型，让它记住“刚刚说过什么”。
        messages = [
            self.add_conversation_message(
                session_id=session_id,
                role="user",
                content=user_input,
                user_id=user_id,
                run_id=run_id,
                attachment_ids=attachment_ids,
            ),
            self.add_conversation_message(
                session_id=session_id,
                role="assistant",
                content=assistant_output,
                user_id=user_id,
                run_id=run_id,
            ),
        ]
        return messages

    def recent_conversation_messages(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        limit: int = DEFAULT_CONVERSATION_TURN_LIMIT * 2,
    ) -> list[ConversationMessage]:
        return self.list_conversation_messages(user_id=user_id, session_id=session_id, limit=limit)

    def cleanup_conversation_history(
        self,
        user_id: str = DEFAULT_USER_ID,
        older_than_days: int = 30,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> ConversationCleanupResult:
        if older_than_days <= 0:
            raise ValueError("older_than_days 必须大于 0。")

        cutoff_at = (now or datetime.now(timezone.utc)) - timedelta(days=older_than_days)
        normalized_session_id = normalize_session_id(session_id) if session_id else None
        with self._connect() as connection:
            with connection.cursor() as cursor:
                select_sql = [
                    "SELECT DISTINCT session_id",
                    "FROM conversation_messages",
                    "WHERE user_id = %s AND created_at < %s",
                ]
                select_params: list[Any] = [user_id, cutoff_at]
                if normalized_session_id:
                    select_sql.append("AND session_id = %s")
                    select_params.append(normalized_session_id)
                cursor.execute("\n".join(select_sql), tuple(select_params))
                affected_sessions = sorted({str(row["session_id"]) for row in cursor.fetchall()})

                delete_sql = [
                    "DELETE FROM conversation_messages",
                    "WHERE user_id = %s AND created_at < %s",
                ]
                delete_params: list[Any] = [user_id, cutoff_at]
                if normalized_session_id:
                    delete_sql.append("AND session_id = %s")
                    delete_params.append(normalized_session_id)
                cursor.execute("\n".join(delete_sql), tuple(delete_params))
                deleted_messages = int(cursor.rowcount)

                deleted_summaries = 0
                if affected_sessions:
                    placeholders = ", ".join(["%s"] * len(affected_sessions))
                    cursor.execute(
                        f"""
                        DELETE FROM conversation_session_summaries
                        WHERE user_id = %s AND session_id IN ({placeholders})
                        """,
                        (user_id, *affected_sessions),
                    )
                    deleted_summaries = int(cursor.rowcount)
            connection.commit()

        return ConversationCleanupResult(
            user_id=user_id,
            session_id=normalized_session_id,
            older_than_days=older_than_days,
            cutoff_at=cutoff_at,
            deleted_messages=deleted_messages,
            deleted_summaries=deleted_summaries,
            affected_sessions=affected_sessions,
        )

    def list_conversation_sessions(
        self,
        user_id: str = DEFAULT_USER_ID,
        limit: int = 20,
        offset: int = 0,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[ConversationSessionRecord], int]:
        """分页列出会话；date_from / date_to 按会话最近活跃日期过滤。"""
        return _list_conversation_sessions(
            self,
            user_id=user_id,
            limit=limit,
            offset=offset,
            keyword=keyword,
            date_from=date_from,
            date_to=date_to,
        )

    def list_conversation_messages(
        self,
        user_id: str = DEFAULT_USER_ID,
        session_id: str = DEFAULT_SESSION_ID,
        limit: int | None = None,
    ) -> list[ConversationMessage]:
        normalized_session_id = normalize_session_id(session_id)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                sql = [
                    "SELECT id, session_id, role, content, run_id, attachment_ids, created_at",
                    "FROM conversation_messages",
                    "WHERE user_id = %s AND session_id = %s",
                ]
                params: list[Any] = [user_id, normalized_session_id]
                if limit is not None:
                    sql.append("ORDER BY created_at DESC, id DESC LIMIT %s")
                    params.append(limit)
                else:
                    sql.append("ORDER BY created_at DESC, id DESC")
                cursor.execute("\n".join(sql), tuple(params))
                rows = cursor.fetchall()
        return [
            ConversationMessage(
                id=int(row["id"]),
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
                run_id=row["run_id"],
                attachment_ids=_parse_attachment_ids(row.get("attachment_ids")),
            )
            for row in rows
        ]

    def recent_memories(self, user_id: str = DEFAULT_USER_ID, limit: int = 5) -> list[str]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT memory_text
                    FROM conversation_memories
                    WHERE user_id = %s
                      AND (expires_at IS NULL OR expires_at > UTC_TIMESTAMP())
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cursor.fetchall()
        return [row["memory_text"] for row in rows]

    def list_memories(self, user_id: str = DEFAULT_USER_ID, limit: int = 20) -> list[MemoryRecord]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, memory_text, source, created_at, expires_at
                    FROM conversation_memories
                    WHERE user_id = %s
                      AND (expires_at IS NULL OR expires_at > UTC_TIMESTAMP())
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cursor.fetchall()
        return [
            MemoryRecord(
                id=int(row["id"]),
                memory_text=row["memory_text"],
                source=row["source"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
            )
            for row in rows
        ]

    def delete_memory(self, memory_id: int, user_id: str = DEFAULT_USER_ID) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM conversation_memories WHERE id = %s AND user_id = %s",
                    (memory_id, user_id),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
        return deleted

    def dedupe_memories(self, user_id: str = DEFAULT_USER_ID) -> MemoryDeduplicationResult:
        # 历史数据里可能已经存在重复记忆。这里不用复杂 SQL，而是在 Python 里按
        # (来源, 归一化后的记忆内容) 分组，保留最早的一条，删除后续重复项。
        # 这样兼容性更好，也方便后续把“精确去重”替换成“语义相似去重”。
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, memory_text, source, created_at
                    FROM conversation_memories
                    WHERE user_id = %s
                    ORDER BY created_at ASC, id ASC
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()

                seen: dict[tuple[str, str], int] = {}
                kept_ids: list[int] = []
                deleted_ids: list[int] = []
                for row in rows:
                    memory_id = int(row["id"])
                    key = (row["source"], normalize_memory_text(row["memory_text"]))
                    if key in seen:
                        deleted_ids.append(memory_id)
                    else:
                        seen[key] = memory_id
                        kept_ids.append(memory_id)

                if deleted_ids:
                    placeholders = ", ".join(["%s"] * len(deleted_ids))
                    cursor.execute(
                        f"""
                        DELETE FROM conversation_memories
                        WHERE user_id = %s AND id IN ({placeholders})
                        """,
                        (user_id, *deleted_ids),
                    )
            connection.commit()

        return MemoryDeduplicationResult(
            scanned_count=len(rows),
            deleted_count=len(deleted_ids),
            kept_ids=kept_ids,
            deleted_ids=deleted_ids,
        )

    def add_task_history(
        self,
        task_type: str,
        user_input: str,
        output_summary: str | None,
        status: str,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO task_history
                        (user_id, task_type, user_input, output_summary, status, error_message, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        task_type,
                        user_input,
                        output_summary,
                        status,
                        error_message,
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
            connection.commit()

    def list_task_history(self, user_id: str = DEFAULT_USER_ID, limit: int = 20) -> list[TaskHistory]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, task_type, user_input, output_summary, status, error_message, created_at
                    FROM task_history
                    WHERE user_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cursor.fetchall()
        return [
            TaskHistory(
                id=int(row["id"]),
                task_type=row["task_type"],
                user_input=row["user_input"],
                output_summary=row["output_summary"],
                status=row["status"],
                error_message=row["error_message"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def add_tool_call_log(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        output_summary: str | None,
        status: str,
        duration_ms: int,
        error_message: str | None = None,
        user_id: str = DEFAULT_USER_ID,
        run_id: str | None = None,
        source_skill: str | None = None,
        retry_count: int | None = None,
        risk_level: str | None = None,
        requires_confirmation: bool | None = None,
        confirmed: bool | None = None,
        failure_stage: str | None = None,
    ) -> None:
        input_payload = _build_tool_log_input_payload(
            inputs=inputs,
            run_id=run_id,
            source_skill=source_skill,
            retry_count=retry_count,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            confirmed=confirmed,
            failure_stage=failure_stage,
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO tool_call_logs
                        (user_id, tool_name, input_json, output_summary, status, error_message, duration_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        tool_name,
                        json.dumps(input_payload, ensure_ascii=False),
                        output_summary,
                        status,
                        error_message,
                        max(0, int(duration_ms)),
                    ),
                )
            connection.commit()

    def list_tool_call_logs(self, user_id: str = DEFAULT_USER_ID, limit: int = 20) -> list[ToolCallLog]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, tool_name, input_json, output_summary, status, error_message,
                           duration_ms, created_at
                    FROM tool_call_logs
                    WHERE user_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cursor.fetchall()
        return [
            ToolCallLog(
                id=int(row["id"]),
                tool_name=row["tool_name"],
                input_json=row["input_json"],
                output_summary=row["output_summary"],
                status=row["status"],
                error_message=row["error_message"],
                duration_ms=int(row["duration_ms"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def add_scheduled_job(
        self,
        *,
        job_type: str,
        title: str,
        schedule_time: str,
        timezone_name: str,
        next_run_at: datetime,
        config: dict[str, Any] | None = None,
        user_id: str = DEFAULT_USER_ID,
        schedule_kind: str = "daily_time",
        status: str = "enabled",
    ) -> ScheduledJob:
        """创建一条定时任务定义。

        这里只保存“任务是什么、何时执行、下次什么时候跑”，
        不负责真正执行任务；真正执行发生在 SchedulerService / worker。
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO scheduled_jobs
                        (user_id, job_type, title, status, schedule_kind, schedule_time, timezone, config_json, next_run_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        job_type,
                        title,
                        status,
                        schedule_kind,
                        schedule_time,
                        timezone_name,
                        json.dumps(config or {}, ensure_ascii=False),
                        next_run_at,
                    ),
                )
                job_id = int(cursor.lastrowid)
            connection.commit()
        return ScheduledJob(
            id=job_id,
            user_id=user_id,
            job_type=job_type,
            title=title,
            status=status,
            schedule_kind=schedule_kind,
            schedule_time=schedule_time,
            timezone=timezone_name,
            config_json=json.dumps(config or {}, ensure_ascii=False),
            next_run_at=next_run_at,
        )

    def list_scheduled_jobs(self, user_id: str = DEFAULT_USER_ID, limit: int = 50) -> list[ScheduledJob]:
        """按创建时间倒序返回某个用户的任务列表。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, user_id, job_type, title, status, schedule_kind, schedule_time, timezone,
                           config_json, last_run_at, next_run_at, created_at, updated_at
                    FROM scheduled_jobs
                    WHERE user_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cursor.fetchall()
        return [_scheduled_job_from_row(row) for row in rows]

    def get_scheduled_job(self, job_id: int, user_id: str = DEFAULT_USER_ID) -> ScheduledJob | None:
        """读取单个任务；查不到时返回 None。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, user_id, job_type, title, status, schedule_kind, schedule_time, timezone,
                           config_json, last_run_at, next_run_at, created_at, updated_at
                    FROM scheduled_jobs
                    WHERE id = %s AND user_id = %s
                    LIMIT 1
                    """,
                    (job_id, user_id),
                )
                row = cursor.fetchone()
        if not row:
            return None
        return _scheduled_job_from_row(row)

    def list_due_scheduled_jobs(self, now: datetime, limit: int = 20) -> list[ScheduledJob]:
        """取出当前已经到点、且仍处于 enabled 状态的任务。

        worker 每一轮扫描本质上就是调用这个查询。
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, user_id, job_type, title, status, schedule_kind, schedule_time, timezone,
                           config_json, last_run_at, next_run_at, created_at, updated_at
                    FROM scheduled_jobs
                    WHERE status = 'enabled' AND next_run_at <= %s
                    ORDER BY next_run_at ASC, id ASC
                    LIMIT %s
                    """,
                    (now, limit),
                )
                rows = cursor.fetchall()
        return [_scheduled_job_from_row(row) for row in rows]

    def update_scheduled_job_runtime(
        self,
        job_id: int,
        *,
        next_run_at: datetime,
        last_run_at: datetime,
    ) -> None:
        """在任务执行后更新运行时间信息。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE scheduled_jobs
                    SET next_run_at = %s, last_run_at = %s
                    WHERE id = %s
                    """,
                    (next_run_at, last_run_at, job_id),
                )
            connection.commit()

    def set_scheduled_job_status(self, job_id: int, status: str, user_id: str = DEFAULT_USER_ID) -> bool:
        """暂停或恢复任务。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE scheduled_jobs
                    SET status = %s
                    WHERE id = %s AND user_id = %s
                    """,
                    (status, job_id, user_id),
                )
                updated = cursor.rowcount
            connection.commit()
        return bool(updated)

    def add_scheduled_job_run(
        self,
        *,
        job_id: int,
        user_id: str,
        status: str,
        started_at: datetime,
        finished_at: datetime | None,
        output_text: str | None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ScheduledJobRun:
        """新增一条任务运行记录。

        这张表保留完整输出和错误信息，主要给前端“运行记录”页和排障使用。
        """
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO scheduled_job_runs
                        (job_id, user_id, status, started_at, finished_at, output_text, error_message, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        user_id,
                        status,
                        started_at,
                        finished_at,
                        output_text,
                        error_message,
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
                run_id = int(cursor.lastrowid)
            connection.commit()
        return ScheduledJobRun(
            id=run_id,
            job_id=job_id,
            user_id=user_id,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            output_text=output_text,
            error_message=error_message,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
        )

    def list_scheduled_job_runs(self, job_id: int, limit: int = 20) -> list[ScheduledJobRun]:
        """查看某个任务最近的执行记录。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, job_id, user_id, status, started_at, finished_at, output_text,
                           error_message, metadata_json, created_at
                    FROM scheduled_job_runs
                    WHERE job_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (job_id, limit),
                )
                rows = cursor.fetchall()
        return [_scheduled_job_run_from_row(row) for row in rows]

    def table_exists(self, table_name: str) -> bool:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                    """,
                    (self.settings.mysql_database, table_name),
                )
                row = cursor.fetchone()
        return bool(row and row["count"])

    def build_agent_context(self, user_id: str = DEFAULT_USER_ID) -> str:
        preferences = self.list_preferences(user_id=user_id)
        memories = self.recent_memories(user_id=user_id)
        if not preferences and not memories:
            return ""

        lines = ["用户长期记忆："]
        if preferences:
            lines.append("偏好：")
            lines.extend(f"- {item.key}: {item.value}" for item in preferences)
        if memories:
            lines.append("最近记忆：")
            lines.extend(f"- {memory}" for memory in memories)
        lines.append("使用这些记忆来减少重复提问，但不要编造记忆里没有的信息。")
        return "\n".join(lines)

    def _connect(self, database: str | None = "__configured__"):
        """借出 MySQL 连接；with 块结束时会归还连接池（init_schema 建库场景仍直连）。"""
        db = self.settings.mysql_database if database == "__configured__" else database
        return borrow_connection(self.settings, database=db)
