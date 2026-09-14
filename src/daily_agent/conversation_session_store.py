"""历史会话分页查询，独立于其余记忆读写逻辑。"""

from typing import Protocol

from daily_agent.conversation_summary import summarize_session_title
from daily_agent.memory_models import ConversationSessionRecord
from daily_agent.memory_support import summarize_text_for_preview


class ConversationSessionConnectionProvider(Protocol):
    """声明分页查询需要的最小数据库连接能力。"""

    def _connect(self): ...


def literal_like_pattern(keyword: str) -> str:
    """把用户关键字转换为 MySQL LIKE 的字面量包含匹配参数。"""
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_conversation_sessions(
    store: ConversationSessionConnectionProvider,
    user_id: str,
    limit: int,
    offset: int,
    keyword: str | None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[list[ConversationSessionRecord], int]:
    """分页列出会话，并批量补齐标题、预览和摘要状态。

    date_from / date_to 按“会话最近一条消息的日期”过滤（date_to 为不包含上界），
    只参与会话聚合条件的过滤，不影响消息计数、会话标题归纳和最后消息预览的统计口径。
    标题以每个会话时间最早的第一条有效 user 消息为准，一旦有效主题产生，
    后续普通追问不会改变标题。
    """
    normalized_keyword = (keyword or "").strip()
    keyword_pattern = literal_like_pattern(normalized_keyword)
    with store._connect() as connection:
        with connection.cursor() as cursor:
            # 搜索条件对 session_id 和同会话消息正文做普通包含匹配。LIKE 参数先转义，
            # 因此用户输入的百分号、下划线和反斜杠都只表示字面字符，不会扩大查询范围。
            search_sql = ""
            search_params: list[str] = []
            if normalized_keyword:
                search_sql = """
                AND (
                    m.session_id LIKE %s ESCAPE '\\\\'
                    OR EXISTS (
                        SELECT 1
                        FROM conversation_messages searched_message
                        WHERE searched_message.user_id = m.user_id
                          AND searched_message.session_id = m.session_id
                          AND searched_message.content LIKE %s ESCAPE '\\\\'
                    )
                )
                """
                search_params = [keyword_pattern, keyword_pattern]
            # 日期范围作用于每个会话的最近活跃时间（MAX(created_at)）。
            # 过滤条件同时放进总数和分页两段 SQL，保证二者语义完全一致。
            date_condition = ""
            if date_from and date_to:
                date_condition = "AND MAX(m.created_at) >= %s AND MAX(m.created_at) < %s"
            elif date_from:
                date_condition = "AND MAX(m.created_at) >= %s"
            elif date_to:
                date_condition = "AND MAX(m.created_at) < %s"
            date_params: list[str] = []
            if date_from and date_to:
                date_params = [date_from, date_to]
            elif date_from:
                date_params = [date_from]
            elif date_to:
                date_params = [date_to]
            cursor.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM (
                    SELECT m.session_id
                    FROM conversation_messages m
                    WHERE m.user_id = %s
                    {search_sql}
                    GROUP BY m.session_id
                    HAVING COUNT(*) > 0
                    {date_condition}
                ) grouped_sessions
                """,
                (user_id, *search_params, *date_params),
            )
            total_row = cursor.fetchone()
            total = int((total_row or {}).get("total") or 0)
            if total == 0:
                return [], 0

            cursor.execute(
                f"""
                SELECT m.session_id, MAX(m.created_at) AS last_message_at, COUNT(*) AS message_count
                FROM conversation_messages m
                WHERE m.user_id = %s
                {search_sql}
                GROUP BY m.session_id
                HAVING COUNT(*) > 0
                {date_condition}
                ORDER BY last_message_at DESC, session_id ASC
                LIMIT %s OFFSET %s
                """,
                (user_id, *search_params, *date_params, limit, offset),
            )
            rows = list(cursor.fetchall())
            if not rows:
                return [], total

            session_ids = [str(row["session_id"]) for row in rows]
            placeholders = ", ".join(["%s"] * len(session_ids))

            # 窗口函数一次性取回每个会话的最后一条消息（预览）和时间最早的最多
            # 4 条 user 消息（标题归纳按时间正序取首个有效文本）。无论本页有多少
            # 会话，标题与预览始终只执行一次批量查询，避免原实现逐会话补查造成 N+1。
            cursor.execute(
                f"""
                SELECT session_id, role, content, latest_rank, first_role_rank
                FROM (
                    SELECT
                        session_id,
                        role,
                        content,
                        ROW_NUMBER() OVER (
                            PARTITION BY session_id
                            ORDER BY created_at DESC, id DESC
                        ) AS latest_rank,
                        ROW_NUMBER() OVER (
                            PARTITION BY session_id, role
                            ORDER BY created_at ASC, id ASC
                        ) AS first_role_rank
                    FROM conversation_messages
                    WHERE user_id = %s AND session_id IN ({placeholders})
                ) ranked_messages
                WHERE latest_rank = 1 OR (role = 'user' AND first_role_rank <= 4)
                """,
                (user_id, *session_ids),
            )
            content_rows = cursor.fetchall()
            user_messages_by_session: dict[str, list[tuple[int, str]]] = {}
            previews: dict[str, str] = {}
            for content_row in content_rows:
                content_session_id = str(content_row["session_id"])
                if content_row["role"] == "user" and int(content_row["first_role_rank"]) <= 4:
                    user_messages_by_session.setdefault(content_session_id, []).append(
                        (
                            int(content_row["first_role_rank"]),
                            str(content_row["content"] or ""),
                        )
                    )
                if int(content_row["latest_rank"]) == 1:
                    previews[content_session_id] = str(content_row["content"] or "")
            # 按 first_role_rank 升序排列等价于按消息时间正序，
            # summarize_session_title 会从头扫描并取第一个有效文本作为标题。
            user_messages_by_session = {
                session_id: [content for _, content in sorted(messages)]
                for session_id, messages in user_messages_by_session.items()
            }

            # 摘要状态和模型生成标题合并成一次批量查询。派生表 selected_sessions
            # 由本页 session_id 构造，因此即使某个会话还没有摘要或标题也会返回；
            # 整个分页流程仍固定为 4 次查询，不随会话数量增长。
            selected_sessions_sql = " UNION ALL ".join(
                ["SELECT %s AS session_id"] * len(session_ids)
            )
            cursor.execute(
                f"""
                SELECT
                    selected_sessions.session_id,
                    summary.session_id IS NOT NULL AS has_summary,
                    semantic_title.title
                FROM ({selected_sessions_sql}) selected_sessions
                LEFT JOIN conversation_session_summaries summary
                    ON summary.user_id = %s
                   AND summary.session_id = selected_sessions.session_id
                LEFT JOIN conversation_session_titles semantic_title
                    ON semantic_title.user_id = %s
                   AND semantic_title.session_id = selected_sessions.session_id
                """,
                (*session_ids, user_id, user_id),
            )
            session_metadata_rows = cursor.fetchall()
            summarized_session_ids = {
                str(metadata_row["session_id"])
                for metadata_row in session_metadata_rows
                if bool(metadata_row.get("has_summary", True))
            }
            semantic_titles = {
                str(metadata_row["session_id"]): str(metadata_row.get("title") or "").strip()
                for metadata_row in session_metadata_rows
                if str(metadata_row.get("title") or "").strip()
            }

    sessions = []
    for row in rows:
        session_id = str(row["session_id"])
        title = semantic_titles.get(session_id) or summarize_session_title(
            user_messages_by_session.get(session_id, []),
            fallback=previews.get(session_id) or session_id,
        )
        preview = previews.get(session_id) or title
        sessions.append(
            ConversationSessionRecord(
                session_id=session_id,
                title=title,
                preview=summarize_text_for_preview(preview),
                message_count=int(row["message_count"]),
                last_message_at=row["last_message_at"],
                has_summary=session_id in summarized_session_ids,
            )
        )
    return sessions, total
