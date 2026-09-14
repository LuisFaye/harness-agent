"""Skill 私有记忆接口。

Skill 只能通过当前 user_id + skill_name 的作用域读写键值，既看不到其他 Skill 的
配置，也不会把内部状态注入 Agent 的全局用户偏好。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Protocol

from daily_agent.factories import StoreFactory

_SENSITIVE_KEY_RE = re.compile(r"(?:password|token|secret|api[_-]?key|密码|密钥)", re.IGNORECASE)
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


@dataclass(frozen=True)
class SkillPreference:
    key: str
    value: str
    updated_at: datetime | None = None


class SkillPreferenceRepository(Protocol):
    def set(self, user_id: str, skill_name: str, key: str, value: str) -> None: ...

    def list(self, user_id: str, skill_name: str) -> list[SkillPreference]: ...

    def delete(self, user_id: str, skill_name: str, key: str) -> bool: ...


class MySQLSkillPreferenceRepository:
    """复用主记忆库连接，但只访问独立的 skill_preferences 表。"""

    def __init__(self, store_factory: StoreFactory) -> None:
        self.store_factory = store_factory

    def set(self, user_id: str, skill_name: str, key: str, value: str) -> None:
        with self.store_factory()._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO skill_preferences (user_id, skill_name, pref_key, pref_value)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE pref_value = VALUES(pref_value)
                    """,
                    (user_id, skill_name, key, value),
                )
            connection.commit()

    def list(self, user_id: str, skill_name: str) -> list[SkillPreference]:
        with self.store_factory()._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT pref_key, pref_value, updated_at
                    FROM skill_preferences
                    WHERE user_id = %s AND skill_name = %s
                    ORDER BY pref_key
                    """,
                    (user_id, skill_name),
                )
                rows = cursor.fetchall()
        return [
            SkillPreference(key=row["pref_key"], value=row["pref_value"], updated_at=row["updated_at"])
            for row in rows
        ]

    def delete(self, user_id: str, skill_name: str, key: str) -> bool:
        with self.store_factory()._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM skill_preferences WHERE user_id = %s AND skill_name = %s AND pref_key = %s",
                    (user_id, skill_name, key),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
        return deleted


@dataclass(frozen=True)
class SkillPrivateMemory:
    user_id: str
    skill_name: str
    repository: SkillPreferenceRepository

    def set(self, key: str, value: str) -> None:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("Skill 私有记忆 key 不能为空。")
        if _SENSITIVE_KEY_RE.search(normalized_key) and not _ENV_NAME_RE.fullmatch(value.strip()):
            raise ValueError("Skill 私有记忆不能保存密钥明文，请只保存环境变量名。")
        self.repository.set(self.user_id, self.skill_name, normalized_key, value)

    def get(self, key: str, default: str | None = None) -> str | None:
        normalized_key = key.strip()
        return next((item.value for item in self.list() if item.key == normalized_key), default)

    def list(self) -> list[SkillPreference]:
        return self.repository.list(self.user_id, self.skill_name)

    def delete(self, key: str) -> bool:
        return self.repository.delete(self.user_id, self.skill_name, key.strip())
