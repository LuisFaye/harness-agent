"""邮箱密码、邀请和服务端会话的持久化认证服务。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from daily_agent.auth_models import AuthenticatedUser
from daily_agent.memory import MySQLMemoryStore


SESSION_COOKIE_NAME = "sb_agent_session"
SESSION_TTL_DAYS = 30
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_MINUTES = 15
RESET_PASSWORD_BYTES = 18
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PASSWORD_FORMAT = "scrypt"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


class AuthError(ValueError):
    """可安全返回给调用方的认证错误。"""

    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class AuthService:
    """认证数据访问层；令牌只保存 SHA-256，密码使用带随机盐的 scrypt。"""

    def __init__(self, store: MySQLMemoryStore | None = None) -> None:
        self.store = store or MySQLMemoryStore()
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def init_schema(self) -> None:
        """复用项目全量建表脚本，保证认证表在首次访问前存在。"""
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self.store.init_schema()
            self._schema_ready = True

    def setup_required(self) -> bool:
        self.init_schema()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS total FROM auth_users")
                row = cursor.fetchone()
        return not row or int(row["total"]) == 0

    def bootstrap_owner(self, *, email: str, password: str, display_name: str) -> AuthenticatedUser:
        """只允许在零用户状态下创建首位所有者，并接管历史 default 数据。"""
        normalized_email = _normalize_email(email)
        normalized_name = _normalize_display_name(display_name)
        password_hash = _hash_password(password)
        user_id = str(uuid.uuid4())
        self.init_schema()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS total FROM auth_users FOR UPDATE")
                if int(cursor.fetchone()["total"]) != 0:
                    raise AuthError("系统已经完成初始化。", code="setup_completed", status_code=409)
                cursor.execute(
                    """
                    INSERT INTO auth_users (id, email, password_hash, display_name, role, status)
                    VALUES (%s, %s, %s, %s, 'owner', 'active')
                    """,
                    (user_id, normalized_email, password_hash, normalized_name),
                )
                self._claim_legacy_rows(cursor, user_id)
                self._audit(cursor, user_id, "owner_bootstrapped", user_id, {"email": normalized_email})
            connection.commit()
        return AuthenticatedUser(user_id, normalized_email, normalized_name, "owner", "active")

    def login(self, *, email: str, password: str) -> tuple[AuthenticatedUser, str, datetime]:
        normalized_email = _normalize_email(email)
        self.init_schema()
        now = _utc_now()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, email, password_hash, display_name, role, status,
                           failed_login_count, locked_until
                    FROM auth_users WHERE email = %s FOR UPDATE
                    """,
                    (normalized_email,),
                )
                row = cursor.fetchone()
                password_valid = _verify_password(password, row["password_hash"] if row else _dummy_hash())
                if not row or not password_valid:
                    if row:
                        self._record_login_failure(cursor, row, now)
                        self._audit(cursor, None, "login_failed", row["id"], {"email": normalized_email})
                    connection.commit()
                    raise AuthError("邮箱或密码错误。", code="invalid_credentials", status_code=401)
                if row["status"] != "active":
                    raise AuthError("账号已被禁用。", code="account_disabled", status_code=403)
                locked_until = _as_utc(row.get("locked_until"))
                if locked_until and locked_until > now:
                    raise AuthError("登录失败次数过多，请稍后再试。", code="account_locked", status_code=429)
                token = secrets.token_urlsafe(48)
                token_hash = _token_hash(token)
                session_id = str(uuid.uuid4())
                expires_at = now + timedelta(days=SESSION_TTL_DAYS)
                cursor.execute(
                    """
                    INSERT INTO auth_sessions (id, user_id, token_hash, expires_at, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (session_id, row["id"], token_hash, _mysql_time(expires_at), _mysql_time(now)),
                )
                cursor.execute(
                    """
                    UPDATE auth_users
                    SET failed_login_count = 0, locked_until = NULL, last_login_at = %s
                    WHERE id = %s
                    """,
                    (_mysql_time(now), row["id"]),
                )
                self._audit(cursor, row["id"], "login_succeeded", row["id"], None)
            connection.commit()
        return _authenticated_user(row), token, expires_at

    def authenticate(self, token: str | None) -> AuthenticatedUser | None:
        if not token:
            return None
        self.init_schema()
        now = _utc_now()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT u.id, u.email, u.display_name, u.role, u.status,
                           s.id AS session_id, s.last_seen_at
                    FROM auth_sessions s
                    JOIN auth_users u ON u.id = s.user_id
                    WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > %s
                    LIMIT 1
                    """,
                    (_token_hash(token), _mysql_time(now)),
                )
                row = cursor.fetchone()
                if not row or row["status"] != "active":
                    return None
                last_seen = _as_utc(row.get("last_seen_at"))
                if last_seen is None or now - last_seen >= timedelta(minutes=5):
                    cursor.execute(
                        "UPDATE auth_sessions SET last_seen_at = %s WHERE id = %s",
                        (_mysql_time(now), row["session_id"]),
                    )
                    connection.commit()
        return _authenticated_user(row)

    def logout(self, token: str | None, *, actor_user_id: str | None = None) -> None:
        if not token:
            return
        self.init_schema()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE auth_sessions SET revoked_at = UTC_TIMESTAMP(6) WHERE token_hash = %s AND revoked_at IS NULL",
                    (_token_hash(token),),
                )
                self._audit(cursor, actor_user_id, "logout", actor_user_id, None)
            connection.commit()

    def create_invite_code(
        self,
        *,
        label: str,
        created_by: str,
        expires_in_days: int | None,
        max_uses: int | None,
    ) -> dict[str, Any]:
        """创建可重复使用的邀请码；明文按产品约定保存，仅所有者接口可以读取。"""
        normalized_label = " ".join(str(label or "").split())[:80]
        code = _generate_invite_code()
        now = _utc_now()
        expires_at = now + timedelta(days=int(expires_in_days)) if expires_in_days is not None else None
        invite_code_id = str(uuid.uuid4())
        self.init_schema()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO auth_invite_codes
                        (id, code, label, max_uses, expires_at, created_by, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (invite_code_id, code, normalized_label, max_uses,
                     _mysql_time(expires_at) if expires_at else None, created_by, _mysql_time(now)),
                )
                self._audit(cursor, created_by, "invite_code_created", None, {"invite_code_id": invite_code_id})
            connection.commit()
        return {
            "id": invite_code_id,
            "code": code,
            "label": normalized_label,
            "status": "active",
            "use_count": 0,
            "max_uses": max_uses,
            "expires_at": expires_at,
            "created_at": now,
        }

    def register_with_invite_code(
        self,
        *,
        code: str,
        email: str,
        password: str,
        display_name: str,
    ) -> AuthenticatedUser:
        """校验并消费一次邀请码使用次数，注册成功后账号立即启用。"""
        normalized_code = _normalize_invite_code(code)
        normalized_email = _normalize_email(email)
        normalized_name = _normalize_display_name(display_name)
        now = _utc_now()
        user_id = str(uuid.uuid4())
        self.init_schema()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, use_count, max_uses, expires_at, revoked_at
                    FROM auth_invite_codes WHERE code = %s FOR UPDATE
                    """,
                    (normalized_code,),
                )
                invite_code = cursor.fetchone()
                if not invite_code or invite_code.get("revoked_at"):
                    raise AuthError("邀请码无效或已被停用。", code="invite_code_invalid", status_code=410)
                expires_at = _as_utc(invite_code.get("expires_at"))
                if expires_at and expires_at <= now:
                    raise AuthError("邀请码已经过期。", code="invite_code_expired", status_code=410)
                use_count = int(invite_code.get("use_count") or 0)
                max_uses = invite_code.get("max_uses")
                if max_uses is not None and use_count >= int(max_uses):
                    raise AuthError("邀请码使用人数已满。", code="invite_code_exhausted", status_code=410)
                cursor.execute("SELECT id FROM auth_users WHERE email = %s", (normalized_email,))
                if cursor.fetchone():
                    raise AuthError("这个邮箱已经注册。", code="email_registered", status_code=409)
                password_hash = _hash_password(password)
                cursor.execute(
                    """
                    INSERT INTO auth_users (id, email, password_hash, display_name, role, status)
                    VALUES (%s, %s, %s, %s, 'member', 'active')
                    """,
                    (user_id, normalized_email, password_hash, normalized_name),
                )
                cursor.execute(
                    "UPDATE auth_invite_codes SET use_count = use_count + 1 WHERE id = %s",
                    (invite_code["id"],),
                )
                cursor.execute(
                    """
                    INSERT INTO auth_invite_code_uses (id, invite_code_id, user_id, used_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (str(uuid.uuid4()), invite_code["id"], user_id, _mysql_time(now)),
                )
                self._audit(cursor, user_id, "invite_code_registered", user_id, {"invite_code_id": invite_code["id"]})
            connection.commit()
        return AuthenticatedUser(user_id, normalized_email, normalized_name, "member", "active")

    def list_users(self) -> list[dict[str, Any]]:
        self.init_schema()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, email, display_name, role, status, created_at, last_login_at
                    FROM auth_users ORDER BY role = 'owner' DESC, created_at ASC
                    """
                )
                rows = cursor.fetchall()
        return [{**row, "is_owner": row["role"] == "owner"} for row in rows]

    def list_invite_codes(self) -> list[dict[str, Any]]:
        self.init_schema()
        now = _utc_now()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, code, label, use_count, max_uses, expires_at, revoked_at, created_at
                    FROM auth_invite_codes ORDER BY created_at DESC LIMIT 100
                    """
                )
                rows = cursor.fetchall()
        return [
            {
                "id": row["id"],
                "code": row["code"],
                "label": row["label"],
                "status": _invite_code_status(row, now),
                "use_count": int(row.get("use_count") or 0),
                "max_uses": int(row["max_uses"]) if row.get("max_uses") is not None else None,
                "expires_at": _as_utc(row["expires_at"]),
                "created_at": _as_utc(row["created_at"]),
            }
            for row in rows
        ]

    def revoke_invite_code(self, invite_code_id: str, *, actor_user_id: str) -> bool:
        self.init_schema()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE auth_invite_codes SET revoked_at = UTC_TIMESTAMP(6)
                    WHERE id = %s AND revoked_at IS NULL
                    """,
                    (invite_code_id,),
                )
                changed = cursor.rowcount > 0
                if changed:
                    self._audit(cursor, actor_user_id, "invite_code_revoked", None, {"invite_code_id": invite_code_id})
            connection.commit()
        return changed

    def set_user_status(self, user_id: str, *, status: str, actor_user_id: str) -> dict[str, Any]:
        if user_id == actor_user_id and status == "disabled":
            raise AuthError("不能禁用当前所有者账号。", code="cannot_disable_self", status_code=409)
        self.init_schema()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT role FROM auth_users WHERE id = %s FOR UPDATE", (user_id,))
                row = cursor.fetchone()
                if not row:
                    raise AuthError("没有找到这个成员。", code="user_not_found", status_code=404)
                if row["role"] == "owner" and status == "disabled":
                    raise AuthError("不能禁用系统所有者。", code="cannot_disable_owner", status_code=409)
                cursor.execute("UPDATE auth_users SET status = %s WHERE id = %s", (status, user_id))
                if status == "disabled":
                    cursor.execute(
                        "UPDATE auth_sessions SET revoked_at = UTC_TIMESTAMP(6) WHERE user_id = %s AND revoked_at IS NULL",
                        (user_id,),
                    )
                self._audit(cursor, actor_user_id, f"user_{status}", user_id, None)
            connection.commit()
        return next(item for item in self.list_users() if item["id"] == user_id)

    def reset_user_password(self, user_id: str, *, actor_user_id: str) -> str:
        """生成随机新密码、撤销成员全部登录会话，并仅向调用方返回一次明文。"""
        new_password = secrets.token_urlsafe(RESET_PASSWORD_BYTES)
        password_hash = _hash_password(new_password)
        now = _utc_now()
        self.init_schema()
        with self.store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, status FROM auth_users WHERE id = %s FOR UPDATE", (user_id,))
                user = cursor.fetchone()
                if not user:
                    raise AuthError("没有找到可重置密码的成员。", code="user_not_found", status_code=404)
                if user["status"] != "active":
                    raise AuthError("账号已被禁用，无法重置密码。", code="account_disabled", status_code=403)
                cursor.execute(
                    """
                    UPDATE auth_users
                    SET password_hash = %s, failed_login_count = 0, locked_until = NULL
                    WHERE id = %s
                    """,
                    (password_hash, user_id),
                )
                cursor.execute(
                    "UPDATE auth_sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
                    (_mysql_time(now), user_id),
                )
                self._audit(cursor, actor_user_id, "password_reset", user_id, None)
            connection.commit()
        return new_password

    def _record_login_failure(self, cursor, row: dict[str, Any], now: datetime) -> None:
        failed = int(row.get("failed_login_count") or 0) + 1
        locked_until = now + timedelta(minutes=LOGIN_LOCK_MINUTES) if failed >= LOGIN_FAILURE_LIMIT else None
        cursor.execute(
            "UPDATE auth_users SET failed_login_count = %s, locked_until = %s WHERE id = %s",
            (failed, _mysql_time(locked_until) if locked_until else None, row["id"]),
        )

    @staticmethod
    def _audit(cursor, actor_user_id: str | None, action: str, target_user_id: str | None,
               detail: dict[str, Any] | None) -> None:
        cursor.execute(
            """
            INSERT INTO auth_audit_logs (actor_user_id, action, target_user_id, detail_json)
            VALUES (%s, %s, %s, %s)
            """,
            (actor_user_id, action, target_user_id, json.dumps(detail, ensure_ascii=False) if detail else None),
        )

    @staticmethod
    def _claim_legacy_rows(cursor, new_user_id: str) -> None:
        """首位所有者接管历史单用户记录；不存在的旧表由全量 schema 保证已经创建。"""
        tables = (
            "user_preferences", "skill_preferences", "conversation_memories", "conversation_messages",
            "conversation_session_summaries", "conversation_session_titles", "task_history", "agent_runs",
            "agent_run_evidence", "tool_call_logs", "react_pause_states", "skill_session_states",
            "scheduled_jobs", "scheduled_job_runs",
        )
        for table in tables:
            cursor.execute(f"UPDATE `{table}` SET user_id = %s WHERE user_id = 'default'", (new_user_id,))


def _normalize_email(email: str) -> str:
    value = str(email or "").strip().lower()
    if len(value) > 254 or not _EMAIL_RE.fullmatch(value):
        raise AuthError("请输入有效的邮箱地址。", code="invalid_email")
    return value


def _normalize_display_name(display_name: str) -> str:
    value = " ".join(str(display_name or "").split())
    if not value or len(value) > 80:
        raise AuthError("显示名称应为 1 到 80 个字符。", code="invalid_display_name")
    return value


def _hash_password(password: str) -> str:
    value = str(password or "")
    if len(value) < 6 or len(value) > 256:
        raise AuthError("密码至少 6 位，且不能超过 256 位。", code="weak_password")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=64)
    return "$".join(
        (_PASSWORD_FORMAT, str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P),
         base64.urlsafe_b64encode(salt).decode("ascii"), base64.urlsafe_b64encode(digest).decode("ascii"))
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        kind, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$", 5)
        if kind != _PASSWORD_FORMAT:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            str(password or "").encode("utf-8"), salt=salt,
            n=int(n_text), r=int(r_text), p=int(p_text), dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_PASSWORD_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = _hash_password(secrets.token_urlsafe(32))
    return _DUMMY_PASSWORD_HASH


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _mysql_time(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _authenticated_user(row: dict[str, Any]) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=str(row["id"]), email=str(row["email"]), display_name=str(row["display_name"]),
        role=str(row["role"]), status=str(row["status"]),
    )


def _invite_code_status(row: dict[str, Any], now: datetime) -> str:
    if row.get("revoked_at"):
        return "revoked"
    expires_at = _as_utc(row.get("expires_at"))
    if expires_at and expires_at <= now:
        return "expired"
    max_uses = row.get("max_uses")
    if max_uses is not None and int(row.get("use_count") or 0) >= int(max_uses):
        return "exhausted"
    return "active"


def _generate_invite_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(10)).decode("ascii").rstrip("=")
    return "SB-" + "-".join(raw[index:index + 4] for index in range(0, len(raw), 4))


def _normalize_invite_code(code: str) -> str:
    value = str(code or "").strip().upper().replace(" ", "")
    if not value or len(value) > 64:
        raise AuthError("请输入有效的邀请码。", code="invite_code_invalid")
    return value
