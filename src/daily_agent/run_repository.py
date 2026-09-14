from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from daily_agent.memory import MySQLMemoryStore

StoreFactory = Callable[[], MySQLMemoryStore]

_RUN_ID_SQL = "JSON_UNQUOTE(JSON_EXTRACT(th.metadata_json, '$.run_id'))"
_SESSION_ID_SQL = (
    "COALESCE("
    "JSON_UNQUOTE(JSON_EXTRACT(th.metadata_json, '$.session_id')), "
    "JSON_UNQUOTE(JSON_EXTRACT(th.metadata_json, '$.prompt_snapshot.session_id'))"
    ")"
)


@dataclass(frozen=True)
class RunHistoryRecord:
    """运行查询使用的内部数据库记录，不会直接作为 API 响应返回。"""

    id: int
    user_id: str
    user_input: str
    output_summary: str | None
    status: str
    error_message: str | None
    metadata_json: str | dict[str, Any] | None
    created_at: datetime | None


class TaskHistoryRunRepository:
    """从 task_history 投影 Agent 运行，保持旧 /history 接口不暴露 metadata。"""

    def __init__(self, store_factory: StoreFactory | None = None) -> None:
        self.store_factory = store_factory or MySQLMemoryStore

    def list_records(
        self,
        *,
        user_id: str,
        status: str | None,
        session_id: str | None,
        keyword: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[RunHistoryRecord], int]:
        conditions, filter_params = _build_filters(status=status, session_id=session_id, keyword=keyword)
        latest_join = _latest_run_join()
        where_sql = " AND ".join(["1 = 1", *conditions])

        # MySQLMemoryStore 目前统一持有连接配置。仓储只借用它的连接入口，避免把
        # metadata 读取方法塞回已经过大的 memory.py；M2 建立物理运行表时可直接替换本仓储。
        store = self.store_factory()
        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) AS total {latest_join} WHERE {where_sql}",
                    (user_id, *filter_params),
                )
                total_row = cursor.fetchone() or {"total": 0}
                cursor.execute(
                    f"""
                    SELECT th.id, th.user_id, th.user_input, th.output_summary,
                           th.status, th.error_message,
                           JSON_REMOVE(
                             th.metadata_json,
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
                           th.created_at
                    {latest_join}
                    WHERE {where_sql}
                    ORDER BY th.created_at DESC, th.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (user_id, *filter_params, limit, offset),
                )
                rows = cursor.fetchall()
        return [_record_from_row(row) for row in rows], int(total_row["total"] or 0)

    def get_record(self, *, user_id: str, run_id: str) -> RunHistoryRecord | None:
        store = self.store_factory()
        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT th.id, th.user_id, th.user_input, th.output_summary,
                           th.status, th.error_message, th.metadata_json, th.created_at
                    FROM task_history th
                    WHERE th.user_id = %s
                      AND th.task_type = 'ask'
                      AND {_RUN_ID_SQL} = %s
                    ORDER BY th.created_at DESC, th.id DESC
                    LIMIT 1
                    """,
                    (user_id, run_id),
                )
                row = cursor.fetchone()
        return _record_from_row(row) if row else None


def _latest_run_join() -> str:
    # 同一个 Skill 会话可能用同一 run_id 经过“缺参、确认、完成”多个阶段。
    # 列表只取每个 run_id 的最新历史行，避免用户看到多条看似重复的运行。
    return f"""
        FROM task_history th
        INNER JOIN (
            SELECT MAX(id) AS latest_id
            FROM task_history
            WHERE user_id = %s
              AND task_type = 'ask'
              AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.run_id')) IS NOT NULL
              AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.run_id')) <> ''
            GROUP BY JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.run_id'))
        ) latest ON latest.latest_id = th.id
    """


def _build_filters(
    *,
    status: str | None,
    session_id: str | None,
    keyword: str | None,
) -> tuple[list[str], list[Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if status:
        conditions.append("th.status = %s")
        params.append(status)
    if session_id:
        conditions.append(f"{_SESSION_ID_SQL} = %s")
        params.append(session_id)
    if keyword:
        pattern = f"%{_escape_like_keyword(keyword)}%"
        conditions.append(f"({_RUN_ID_SQL} LIKE %s ESCAPE '=' OR th.user_input LIKE %s ESCAPE '=')")
        params.extend([pattern, pattern])
    return conditions, params


def _escape_like_keyword(value: str) -> str:
    """把 LIKE 通配符转成普通字符，避免输入 % 或 _ 时意外匹配全部记录。"""
    return value.replace("=", "==").replace("%", "=%").replace("_", "=_")


def _record_from_row(row: dict[str, Any]) -> RunHistoryRecord:
    return RunHistoryRecord(
        id=int(row["id"]),
        user_id=str(row["user_id"]),
        user_input=str(row.get("user_input") or ""),
        output_summary=row.get("output_summary"),
        status=str(row.get("status") or "unknown"),
        error_message=row.get("error_message"),
        metadata_json=row.get("metadata_json"),
        created_at=row.get("created_at"),
    )
