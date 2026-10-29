"""Agent 运行生命周期和流式事件的 MySQL 仓储。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from daily_agent.errors import DailyAgentError, MemoryError as DailyAgentMemoryError
from daily_agent.memory import MySQLMemoryStore
from daily_agent.memory_support import (
    normalize_attachment_ids,
    normalize_conversation_text,
    normalize_session_id,
)
from daily_agent.stream_protocol import StreamEvent

StoreFactory = Callable[[], MySQLMemoryStore]


@dataclass(frozen=True)
class RunLifecycleRecord:
    """取消、事件补拉和运行详情共同使用的轻量运行记录。"""

    run_id: str
    user_id: str
    session_id: str
    status: str
    query_text: str
    response_text: str | None
    execution_source: str | None
    model_id: str | None
    error_stage: str | None
    error_message: str | None
    duration_ms: int | None
    metadata_json: str | dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None = None
    cancelled_at: datetime | None = None


@dataclass(frozen=True)
class StoredRunEvent:
    """从 agent_run_events 读取的一条事件。"""

    run_id: str
    sequence: int
    event_type: str
    event_timestamp: str
    payload: dict[str, Any]


class MySQLRunLifecycleRepository:
    """保存运行主记录和按 sequence 排序的事件。"""

    def __init__(self, store_factory: StoreFactory | None = None) -> None:
        self.store_factory = store_factory or MySQLMemoryStore

    def create_run(
        self,
        *,
        run_id: str,
        user_id: str,
        session_id: str,
        query_text: str,
        status: str = "running",
        started_at: datetime | None = None,
        persist_user_message: bool = False,
        attachment_ids: list[str] | None = None,
    ) -> None:
        started = _to_mysql_datetime(started_at or datetime.now(timezone.utc))
        normalized_session_id = normalize_session_id(session_id)
        normalized_query_text = normalize_conversation_text(query_text)
        normalized_attachment_ids = normalize_attachment_ids(attachment_ids)
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO agent_runs
                            (run_id, user_id, session_id, status, query_text, started_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (run_id, user_id, normalized_session_id, status, normalized_query_text, started),
                    )
                    # 页面刷新会清空前端的乐观消息，因此用户消息必须和运行记录在同一事务中落库。
                    # 这样无论 Agent 尚在执行、浏览器断开还是后续步骤失败，会话历史都能恢复原始输入。
                    if persist_user_message:
                        cursor.execute(
                            """
                            INSERT INTO conversation_messages
                                (user_id, session_id, role, content, run_id, attachment_ids)
                            VALUES (%s, %s, 'user', %s, %s, %s)
                            """,
                            (
                                user_id,
                                normalized_session_id,
                                normalized_query_text,
                                run_id,
                                json.dumps(normalized_attachment_ids, ensure_ascii=False)
                                if normalized_attachment_ids
                                else None,
                            ),
                        )
                    else:
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
                        recent_rows = cursor.fetchall() or []
                        trailing_assistant_ids: list[int] = []
                        for row in recent_rows:
                            if row.get("role") == "assistant":
                                trailing_assistant_ids.append(int(row["id"]))
                            else:
                                break
                        if trailing_assistant_ids:
                            placeholders = ", ".join(["%s"] * len(trailing_assistant_ids))
                            cursor.execute(
                                f"""
                                DELETE FROM conversation_messages
                                WHERE user_id = %s AND session_id = %s AND id IN ({placeholders})
                                """,
                                (user_id, normalized_session_id, *trailing_assistant_ids),
                            )
                connection.commit()
        except DailyAgentError:
            raise
        except Exception as exc:
            raise DailyAgentMemoryError(f"创建 Agent 运行记录失败：{exc}") from exc

    def append_event(self, event: StreamEvent) -> None:
        self.append_events([event])

    def append_events(self, events: list[StreamEvent]) -> None:
        if not events:
            return
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    for event in events:
                        self._insert_event(cursor, event)
                connection.commit()
        except DailyAgentError:
            raise
        except Exception as exc:
            raise DailyAgentMemoryError(f"保存 Agent 运行事件失败：{exc}") from exc

    def request_cancel(
        self,
        *,
        run_id: str,
        user_id: str,
        requested_at: datetime | None = None,
    ) -> tuple[str | None, bool]:
        """原子读取状态并把可取消运行更新为 cancel_requested。"""
        requested = _to_mysql_datetime(requested_at or datetime.now(timezone.utc))
        terminal_statuses = {"success", "error", "max_iterations", "cancelled", "needs_input", "needs_confirmation"}
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT status
                        FROM agent_runs
                        WHERE run_id = %s AND user_id = %s
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (run_id, user_id),
                    )
                    row = cursor.fetchone()
                    if not row:
                        return None, False
                    current_status = str(row["status"])
                    if current_status == "cancel_requested" or current_status in terminal_statuses:
                        return current_status, False
                    cursor.execute(
                        """
                        UPDATE agent_runs
                        SET status = 'cancel_requested', cancel_requested_at = %s
                        WHERE run_id = %s AND user_id = %s
                        """,
                        (requested, run_id, user_id),
                    )
                connection.commit()
            return "cancel_requested", True
        except DailyAgentError:
            raise
        except Exception as exc:
            raise DailyAgentMemoryError(f"提交 Agent 运行取消请求失败：{exc}") from exc

    def finalize_run(
        self,
        *,
        run_id: str,
        user_id: str,
        status: str,
        response_text: str,
        execution_source: str | None,
        model_id: str | None,
        error_stage: str | None,
        error_message: str | None,
        metadata: dict[str, Any],
        terminal_event: StreamEvent,
        finished_at: datetime | None = None,
    ) -> None:
        finished = _to_mysql_datetime(finished_at or datetime.now(timezone.utc))
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE agent_runs
                        SET status = %s,
                            response_text = %s,
                            execution_source = %s,
                            model_id = %s,
                            error_stage = %s,
                            error_message = %s,
                            duration_ms = GREATEST(0, TIMESTAMPDIFF(MICROSECOND, started_at, %s) DIV 1000),
                            metadata_json = %s,
                            finished_at = %s,
                            cancelled_at = CASE WHEN %s = 'cancelled' THEN %s ELSE cancelled_at END
                        WHERE run_id = %s AND user_id = %s
                        """,
                        (
                            status,
                            response_text,
                            execution_source,
                            model_id,
                            error_stage,
                            error_message,
                            finished,
                            json.dumps(metadata, ensure_ascii=False),
                            finished,
                            status,
                            finished,
                            run_id,
                            user_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise DailyAgentMemoryError("没有找到需要完成的 Agent 运行记录。")
                    self._insert_event(cursor, terminal_event)
                connection.commit()
        except DailyAgentError:
            raise
        except Exception as exc:
            raise DailyAgentMemoryError(f"完成 Agent 运行记录失败：{exc}") from exc

    def get_run(self, *, user_id: str, run_id: str) -> RunLifecycleRecord | None:
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT run_id, user_id, session_id, status, query_text, response_text,
                               execution_source, model_id, error_stage, error_message,
                               duration_ms, metadata_json, started_at, finished_at,
                               cancel_requested_at, cancelled_at
                        FROM agent_runs
                        WHERE run_id = %s AND user_id = %s
                        LIMIT 1
                        """,
                        (run_id, user_id),
                    )
                    row = cursor.fetchone()
        except DailyAgentError:
            raise
        except Exception as exc:
            raise DailyAgentMemoryError(f"读取 Agent 运行记录失败：{exc}") from exc
        return _run_from_row(row) if row else None

    def list_unmirrored_runs(
        self,
        *,
        user_id: str,
        status: str | None,
        session_id: str | None,
        keyword: str | None,
        limit: int,
    ) -> tuple[list[RunLifecycleRecord], int]:
        """查询尚未写入 task_history 的物理运行。

        正常完成的运行会继续双写 task_history。这里只补充 running、异常中断等
        尚未出现于旧历史表的运行，从源头避免两张表合并时产生重复。
        """
        conditions = [
            "ar.user_id = %s",
            """
            NOT EXISTS (
                SELECT 1
                FROM task_history th
                WHERE th.user_id = ar.user_id
                  AND th.task_type = 'ask'
                  AND JSON_UNQUOTE(JSON_EXTRACT(th.metadata_json, '$.run_id')) = ar.run_id
            )
            """,
        ]
        params: list[Any] = [user_id]
        if status:
            conditions.append("ar.status = %s")
            params.append(status)
        if session_id:
            conditions.append("ar.session_id = %s")
            params.append(session_id)
        if keyword:
            pattern = f"%{_escape_like_keyword(keyword)}%"
            conditions.append("(ar.run_id LIKE %s ESCAPE '=' OR ar.query_text LIKE %s ESCAPE '=')")
            params.extend([pattern, pattern])
        where_sql = " AND ".join(conditions)
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) AS total FROM agent_runs ar WHERE {where_sql}", tuple(params))
                    total_row = cursor.fetchone() or {"total": 0}
                    cursor.execute(
                        f"""
                        SELECT run_id, user_id, session_id, status, query_text, response_text,
                               execution_source, model_id, error_stage, error_message,
                               duration_ms,
                               JSON_REMOVE(
                                 metadata_json,
                                 '$.model_requests',
                                 '$.prompt_snapshot.instructions',
                                 '$.prompt_snapshot.prompt_text',
                                 '$.trace',
                                 '$.observations',
                                 '$.working_state',
                                 '$.loop_events',
                                 '$.citation_bundle',
                                 '$.extracted_memories'
                               ) AS metadata_json,
                               started_at, finished_at,
                               cancel_requested_at, cancelled_at
                        FROM agent_runs ar
                        WHERE {where_sql}
                        ORDER BY ar.started_at DESC, ar.run_id DESC
                        LIMIT %s
                        """,
                        (*params, limit),
                    )
                    rows = cursor.fetchall()
        except DailyAgentError:
            raise
        except Exception as exc:
            raise DailyAgentMemoryError(f"查询 Agent 运行列表失败：{exc}") from exc
        return [_run_from_row(row) for row in rows], int(total_row["total"] or 0)

    def list_events(
        self,
        *,
        run_id: str,
        after_sequence: int,
        limit: int,
    ) -> tuple[list[StoredRunEvent], bool]:
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT run_id, sequence, event_type, event_timestamp, payload_json
                        FROM agent_run_events
                        WHERE run_id = %s AND sequence > %s
                        ORDER BY sequence ASC
                        LIMIT %s
                        """,
                        (run_id, after_sequence, limit + 1),
                    )
                    rows = cursor.fetchall()
        except DailyAgentError:
            raise
        except Exception as exc:
            raise DailyAgentMemoryError(f"读取 Agent 运行事件失败：{exc}") from exc
        has_more = len(rows) > limit
        return [_event_from_row(row) for row in rows[:limit]], has_more

    @staticmethod
    def _insert_event(cursor: Any, event: StreamEvent) -> None:
        cursor.execute(
            """
            INSERT INTO agent_run_events
                (run_id, sequence, event_type, event_timestamp, payload_json)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                event.run_id,
                event.sequence,
                event.type,
                event.timestamp,
                json.dumps(event.payload, ensure_ascii=False),
            ),
        )


def _run_from_row(row: dict[str, Any]) -> RunLifecycleRecord:
    return RunLifecycleRecord(
        run_id=str(row["run_id"]),
        user_id=str(row["user_id"]),
        session_id=str(row["session_id"]),
        status=str(row["status"]),
        query_text=str(row.get("query_text") or ""),
        response_text=row.get("response_text"),
        execution_source=row.get("execution_source"),
        model_id=row.get("model_id"),
        error_stage=row.get("error_stage"),
        error_message=row.get("error_message"),
        duration_ms=int(row["duration_ms"]) if row.get("duration_ms") is not None else None,
        metadata_json=row.get("metadata_json"),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        cancel_requested_at=row.get("cancel_requested_at"),
        cancelled_at=row.get("cancelled_at"),
    )


def _event_from_row(row: dict[str, Any]) -> StoredRunEvent:
    payload = row.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    return StoredRunEvent(
        run_id=str(row["run_id"]),
        sequence=int(row["sequence"]),
        event_type=str(row["event_type"]),
        event_timestamp=str(row["event_timestamp"]),
        payload=payload if isinstance(payload, dict) else {},
    )


def _to_mysql_datetime(value: datetime) -> datetime:
    """MySQL DATETIME 不保存时区，统一转换为 UTC 后去掉 tzinfo。"""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _escape_like_keyword(value: str) -> str:
    return value.replace("=", "==").replace("%", "=%").replace("_", "=_")
