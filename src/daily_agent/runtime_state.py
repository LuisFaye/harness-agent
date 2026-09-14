from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from daily_agent.errors import DailyAgentError
from daily_agent.memory import MySQLMemoryStore
from daily_agent.paths import AppPaths

REACT_PAUSE_LEGACY_PATH = Path.home() / ".daily-agent" / "react_pause_sessions.json"
SKILL_SESSION_LEGACY_PATH = Path.home() / ".daily-agent" / "skill_sessions.json"
DEFAULT_RUNTIME_STATE_TTL_HOURS = 24

StoreFactory = Callable[[], MySQLMemoryStore]


@dataclass(frozen=True)
class ReactPauseStateRecord:
    user_id: str
    session_id: str
    run_id: str
    status: str
    payload: dict[str, Any]
    updated_at: str
    expires_at: str | None = None


@dataclass(frozen=True)
class SkillSessionStateRecord:
    user_id: str
    session_id: str
    skill_name: str
    tool_name: str | None
    interaction_mode: str
    run_id: str | None
    status: str
    inputs: dict[str, Any]
    state: dict[str, Any]
    updated_at: str
    expires_at: str | None = None


class RuntimeStateRepository:
    def __init__(self, store_factory: StoreFactory | None = None, *, paths: AppPaths | None = None) -> None:
        self.store_factory = store_factory or MySQLMemoryStore
        resolved_paths = paths or AppPaths.discover()
        self.react_pause_legacy_path = resolved_paths.react_pause_legacy_path
        self.skill_session_legacy_path = resolved_paths.skill_session_legacy_path

    def get_react_pause_state(self, *, user_id: str, session_id: str) -> ReactPauseStateRecord | None:
        row = self._get_one(
            table_name="react_pause_states",
            user_id=user_id,
            session_id=session_id,
            columns="run_id, status, payload_json, updated_at, expires_at",
        )
        if not row:
            return None
        if _is_expired(row.get("expires_at")):
            self.clear_react_pause_state(user_id=user_id, session_id=session_id)
            return None
        return ReactPauseStateRecord(
            user_id=user_id,
            session_id=session_id,
            run_id=str(row.get("run_id") or ""),
            status=str(row.get("status") or "paused"),
            payload=_coerce_json_object(row.get("payload_json")),
            updated_at=_to_iso(row.get("updated_at")) or utc_now_iso(),
            expires_at=_to_iso(row.get("expires_at")),
        )

    def upsert_react_pause_state(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        status: str,
        payload: dict[str, Any],
        expires_at: str | datetime | None = None,
    ) -> None:
        self._upsert(
            """
            INSERT INTO react_pause_states
                (user_id, session_id, run_id, status, payload_json, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                run_id = VALUES(run_id),
                status = VALUES(status),
                payload_json = VALUES(payload_json),
                expires_at = VALUES(expires_at)
            """,
            (
                user_id,
                session_id,
                run_id,
                status,
                json.dumps(payload, ensure_ascii=False),
                _normalize_datetime(expires_at),
            ),
        )

    def clear_react_pause_state(self, *, user_id: str, session_id: str) -> bool:
        return self._clear(table_name="react_pause_states", user_id=user_id, session_id=session_id)

    def get_skill_session_state(self, *, user_id: str, session_id: str) -> SkillSessionStateRecord | None:
        row = self._get_one(
            table_name="skill_session_states",
            user_id=user_id,
            session_id=session_id,
            columns=(
                "skill_name, tool_name, interaction_mode, run_id, status, "
                "inputs_json, state_json, updated_at, expires_at"
            ),
        )
        if not row:
            return None
        if _is_expired(row.get("expires_at")):
            self.clear_skill_session_state(user_id=user_id, session_id=session_id)
            return None
        return SkillSessionStateRecord(
            user_id=user_id,
            session_id=session_id,
            skill_name=str(row.get("skill_name") or ""),
            tool_name=str(row.get("tool_name")) if row.get("tool_name") is not None else None,
            interaction_mode=str(row.get("interaction_mode") or "deterministic"),
            run_id=str(row.get("run_id")) if row.get("run_id") is not None else None,
            status=str(row.get("status") or "pending"),
            inputs=_coerce_json_object(row.get("inputs_json")),
            state=_coerce_json_object(row.get("state_json")),
            updated_at=_to_iso(row.get("updated_at")) or utc_now_iso(),
            expires_at=_to_iso(row.get("expires_at")),
        )

    def upsert_skill_session_state(
        self,
        *,
        user_id: str,
        session_id: str,
        skill_name: str,
        tool_name: str | None,
        interaction_mode: str,
        run_id: str | None,
        status: str,
        inputs: dict[str, Any],
        state: dict[str, Any],
        expires_at: str | datetime | None = None,
    ) -> None:
        self._upsert(
            """
            INSERT INTO skill_session_states
                (user_id, session_id, skill_name, tool_name, interaction_mode, run_id, status, inputs_json, state_json, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                skill_name = VALUES(skill_name),
                tool_name = VALUES(tool_name),
                interaction_mode = VALUES(interaction_mode),
                run_id = VALUES(run_id),
                status = VALUES(status),
                inputs_json = VALUES(inputs_json),
                state_json = VALUES(state_json),
                expires_at = VALUES(expires_at)
            """,
            (
                user_id,
                session_id,
                skill_name,
                tool_name,
                interaction_mode,
                run_id,
                status,
                json.dumps(inputs, ensure_ascii=False),
                json.dumps(state, ensure_ascii=False),
                _normalize_datetime(expires_at),
            ),
        )

    def clear_skill_session_state(self, *, user_id: str, session_id: str) -> bool:
        return self._clear(table_name="skill_session_states", user_id=user_id, session_id=session_id)

    def _get_one(
        self,
        *,
        table_name: str,
        user_id: str,
        session_id: str,
        columns: str,
    ) -> dict[str, Any] | None:
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT {columns}
                        FROM {table_name}
                        WHERE user_id = %s AND session_id = %s
                        LIMIT 1
                        """,
                        (user_id, session_id),
                    )
                    return cursor.fetchone()
        except Exception as exc:
            raise DailyAgentError(f"读取运行时状态失败：{exc}") from exc

    def _upsert(self, sql: str, params: tuple[Any, ...]) -> None:
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                connection.commit()
        except Exception as exc:
            raise DailyAgentError(f"写入运行时状态失败：{exc}") from exc

    def _clear(self, *, table_name: str, user_id: str, session_id: str) -> bool:
        try:
            store = self.store_factory()
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"DELETE FROM {table_name} WHERE user_id = %s AND session_id = %s",
                        (user_id, session_id),
                    )
                    deleted = cursor.rowcount > 0
                connection.commit()
            return deleted
        except Exception as exc:
            raise DailyAgentError(f"清理运行时状态失败：{exc}") from exc


class JsonStateFile:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DailyAgentError(f"读取运行时状态失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise DailyAgentError("运行时状态文件格式错误，根节点必须是对象。")
        return payload

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def get(self, *, user_id: str, session_id: str) -> dict[str, Any] | None:
        return self.load().get(_make_key(user_id=user_id, session_id=session_id))

    def upsert(self, *, user_id: str, session_id: str, payload: dict[str, Any]) -> None:
        content = self.load()
        content[_make_key(user_id=user_id, session_id=session_id)] = payload
        self.save(content)

    def clear(self, *, user_id: str, session_id: str) -> bool:
        content = self.load()
        key = _make_key(user_id=user_id, session_id=session_id)
        existed = key in content
        if existed:
            content.pop(key, None)
            self.save(content)
        return existed


def build_state_expiry(hours: int = DEFAULT_RUNTIME_STATE_TTL_HOURS) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=max(1, int(hours)))).isoformat()


def generate_run_id(prefix: str = "run") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{timestamp}-{datetime.now(timezone.utc).microsecond:06d}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_key(*, user_id: str, session_id: str) -> str:
    return f"{user_id}:{session_id}"


def _normalize_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    return text or None


def _is_expired(value: Any, now: datetime | None = None) -> bool:
    expires_at = _normalize_datetime(_to_iso(value))
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return expires_at <= current


def _coerce_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}
