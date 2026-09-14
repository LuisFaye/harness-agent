"""指定成员只读会话分享与复制服务。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from daily_agent.memory import MySQLMemoryStore


class SharingError(ValueError):
    """可安全返回给前端的分享业务错误。"""

    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class SharingService:
    """所有分享都绑定发送者、接收者和会话，不生成匿名公开地址。"""

    def __init__(self, store: MySQLMemoryStore | None = None) -> None:
        self.store = store or MySQLMemoryStore()

    def create_share(self, *, owner_user_id: str, session_id: str, recipient_email: str) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        normalized_email = str(recipient_email or "").strip().lower()
        if not normalized_session_id:
            raise SharingError("会话 ID 不能为空。", code="session_required")
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, display_name, status FROM auth_users WHERE email = %s",
                    (normalized_email,),
                )
                recipient = cursor.fetchone()
                if not recipient or recipient["status"] != "active":
                    raise SharingError("没有找到这个启用成员。", code="recipient_not_found", status_code=404)
                if recipient["id"] == owner_user_id:
                    raise SharingError("不能把会话分享给自己。", code="cannot_share_self", status_code=409)
                cursor.execute(
                    "SELECT 1 FROM conversation_messages WHERE user_id = %s AND session_id = %s LIMIT 1",
                    (owner_user_id, normalized_session_id),
                )
                if not cursor.fetchone():
                    raise SharingError("当前账号下没有找到这个会话。", code="session_not_found", status_code=404)
                cursor.execute(
                    """
                    SELECT id, created_at FROM conversation_shares
                    WHERE owner_user_id = %s AND recipient_user_id = %s AND session_id = %s
                      AND revoked_at IS NULL
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (owner_user_id, recipient["id"], normalized_session_id),
                )
                existing = cursor.fetchone()
                if existing:
                    share_id = existing["id"]
                    created_at = existing["created_at"]
                else:
                    share_id = str(uuid.uuid4())
                    created_at = _mysql_now()
                    cursor.execute(
                        """
                        INSERT INTO conversation_shares
                            (id, owner_user_id, recipient_user_id, session_id, permission, created_at)
                        VALUES (%s, %s, %s, %s, 'read_only', %s)
                        """,
                        (share_id, owner_user_id, recipient["id"], normalized_session_id, created_at),
                    )
            connection.commit()
        return {
            "id": share_id,
            "session_id": normalized_session_id,
            "recipient_user_id": recipient["id"],
            "recipient_email": normalized_email,
            "recipient_display_name": recipient["display_name"],
            "permission": "read_only",
            "created_at": created_at,
        }

    def list_received(self, recipient_user_id: str) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.id, s.session_id, s.permission, s.created_at,
                           owner.display_name AS owner_display_name, owner.email AS owner_email,
                           COALESCE(title.title, LEFT(first_message.content, 64), s.session_id) AS title,
                           message_stats.message_count, message_stats.last_message_at
                    FROM conversation_shares s
                    JOIN auth_users owner ON owner.id = s.owner_user_id
                    JOIN (
                        SELECT user_id, session_id, COUNT(*) AS message_count, MAX(created_at) AS last_message_at
                        FROM conversation_messages GROUP BY user_id, session_id
                    ) message_stats ON message_stats.user_id = s.owner_user_id
                        AND message_stats.session_id = s.session_id
                    LEFT JOIN conversation_session_titles title ON title.user_id = s.owner_user_id
                        AND title.session_id = s.session_id
                    LEFT JOIN conversation_messages first_message ON first_message.id = (
                        SELECT MIN(m.id) FROM conversation_messages m
                        WHERE m.user_id = s.owner_user_id AND m.session_id = s.session_id AND m.role = 'user'
                    )
                    WHERE s.recipient_user_id = %s AND s.revoked_at IS NULL
                    ORDER BY s.created_at DESC
                    """,
                    (recipient_user_id,),
                )
                return list(cursor.fetchall())

    def list_sent(self, owner_user_id: str, session_id: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT s.id, s.session_id, s.permission, s.created_at,
                   recipient.id AS recipient_user_id, recipient.email AS recipient_email,
                   recipient.display_name AS recipient_display_name
            FROM conversation_shares s
            JOIN auth_users recipient ON recipient.id = s.recipient_user_id
            WHERE s.owner_user_id = %s AND s.revoked_at IS NULL
        """
        params: list[Any] = [owner_user_id]
        if session_id:
            sql += " AND s.session_id = %s"
            params.append(session_id)
        sql += " ORDER BY s.created_at DESC"
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, tuple(params))
                return list(cursor.fetchall())

    def get_received_share(self, *, share_id: str, recipient_user_id: str) -> dict[str, Any]:
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.id, s.owner_user_id, s.recipient_user_id, s.session_id, s.permission, s.created_at,
                           owner.display_name AS owner_display_name, owner.email AS owner_email,
                           COALESCE(title.title, s.session_id) AS title
                    FROM conversation_shares s
                    JOIN auth_users owner ON owner.id = s.owner_user_id
                    LEFT JOIN conversation_session_titles title ON title.user_id = s.owner_user_id
                        AND title.session_id = s.session_id
                    WHERE s.id = %s AND s.recipient_user_id = %s AND s.revoked_at IS NULL
                    """,
                    (share_id, recipient_user_id),
                )
                share = cursor.fetchone()
                if not share:
                    raise SharingError("分享不存在或已被撤销。", code="share_not_found", status_code=404)
                cursor.execute(
                    """
                    SELECT id, session_id, role, content, run_id, created_at
                    FROM conversation_messages
                    WHERE user_id = %s AND session_id = %s
                    ORDER BY created_at ASC, id ASC LIMIT 200
                    """,
                    (share["owner_user_id"], share["session_id"]),
                )
                share["messages"] = list(cursor.fetchall())
        return share

    def revoke_share(self, *, share_id: str, owner_user_id: str) -> bool:
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE conversation_shares SET revoked_at = UTC_TIMESTAMP(6)
                    WHERE id = %s AND owner_user_id = %s AND revoked_at IS NULL
                    """,
                    (share_id, owner_user_id),
                )
                changed = cursor.rowcount > 0
            connection.commit()
        return changed

    def copy_received_share(self, *, share_id: str, recipient_user_id: str) -> str:
        """把可见消息复制为接收者的新会话；不复制运行权限、私有附件或连接。"""
        target_session_id = f"shared-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, owner_user_id, session_id FROM conversation_shares
                    WHERE id = %s AND recipient_user_id = %s AND revoked_at IS NULL FOR UPDATE
                    """,
                    (share_id, recipient_user_id),
                )
                share = cursor.fetchone()
                if not share:
                    raise SharingError("分享不存在或已被撤销。", code="share_not_found", status_code=404)
                cursor.execute(
                    """
                    SELECT target_session_id FROM conversation_session_copies
                    WHERE source_share_id = %s AND target_user_id = %s
                    """,
                    (share_id, recipient_user_id),
                )
                existing = cursor.fetchone()
                if existing:
                    return str(existing["target_session_id"])
                cursor.execute(
                    """
                    INSERT INTO conversation_messages (user_id, session_id, role, content, run_id, created_at)
                    SELECT %s, %s, role, content, NULL, created_at
                    FROM conversation_messages
                    WHERE user_id = %s AND session_id = %s
                    ORDER BY created_at ASC, id ASC
                    """,
                    (recipient_user_id, target_session_id, share["owner_user_id"], share["session_id"]),
                )
                if cursor.rowcount == 0:
                    raise SharingError("原会话已经没有可复制的消息。", code="share_empty", status_code=409)
                cursor.execute(
                    """
                    INSERT INTO conversation_session_titles (user_id, session_id, title, source)
                    SELECT %s, %s, LEFT(CONCAT(title, '（共享副本）'), 64), 'manual'
                    FROM conversation_session_titles WHERE user_id = %s AND session_id = %s
                    """,
                    (recipient_user_id, target_session_id, share["owner_user_id"], share["session_id"]),
                )
                cursor.execute(
                    """
                    INSERT INTO conversation_session_copies
                        (id, source_share_id, source_owner_user_id, source_session_id,
                         target_user_id, target_session_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (str(uuid.uuid4()), share_id, share["owner_user_id"], share["session_id"],
                     recipient_user_id, target_session_id),
                )
            connection.commit()
        return target_session_id


def _mysql_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
