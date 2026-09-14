from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError
from daily_agent.runtime_state import (
    DEFAULT_RUNTIME_STATE_TTL_HOURS,
    JsonStateFile,
    RuntimeStateRepository,
    SKILL_SESSION_LEGACY_PATH,
    SkillSessionStateRecord,
    build_state_expiry,
    utc_now_iso,
)


@dataclass(frozen=True)
class SkillSessionState:
    user_id: str
    session_id: str
    skill_name: str
    tool_name: str | None
    status: str
    inputs: dict[str, Any]
    state: dict[str, Any]
    updated_at: str
    run_id: str | None = None
    interaction_mode: str = "deterministic"
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "skill_name": self.skill_name,
            "tool_name": self.tool_name,
            "status": self.status,
            "inputs": self.inputs,
            "state": self.state,
            "updated_at": self.updated_at,
            "run_id": self.run_id,
            "interaction_mode": self.interaction_mode,
            "expires_at": self.expires_at,
        }


class SkillSessionStore:
    def __init__(
        self,
        path: Path | None = None,
        state_repository: RuntimeStateRepository | None = None,
        ttl_hours: int = DEFAULT_RUNTIME_STATE_TTL_HOURS,
    ) -> None:
        self.path = path.resolve() if path else None
        self.file_store = JsonStateFile(self.path or SKILL_SESSION_LEGACY_PATH)
        self.state_repository = None if path else (state_repository or RuntimeStateRepository())
        self.ttl_hours = max(1, int(ttl_hours))

    def get(self, *, user_id: str, session_id: str) -> SkillSessionState | None:
        if self.state_repository is None:
            return self._get_from_file(user_id=user_id, session_id=session_id)

        try:
            record = self.state_repository.get_skill_session_state(user_id=user_id, session_id=session_id)
            if record:
                return _state_from_record(record)
            migrated = self._get_from_file(user_id=user_id, session_id=session_id)
            if migrated:
                self.save(migrated)
                self.file_store.clear(user_id=user_id, session_id=session_id)
            return migrated
        except DailyAgentError:
            return self._get_from_file(user_id=user_id, session_id=session_id)

    def save(self, session: SkillSessionState) -> SkillSessionState:
        if self.state_repository is None:
            self.file_store.upsert(user_id=session.user_id, session_id=session.session_id, payload=session.to_dict())
            return session

        try:
            self.state_repository.upsert_skill_session_state(
                user_id=session.user_id,
                session_id=session.session_id,
                skill_name=session.skill_name,
                tool_name=session.tool_name,
                interaction_mode=session.interaction_mode,
                run_id=session.run_id,
                status=session.status,
                inputs=session.inputs,
                state=session.state,
                expires_at=session.expires_at or build_state_expiry(self.ttl_hours),
            )
            return session
        except DailyAgentError:
            self.file_store.upsert(user_id=session.user_id, session_id=session.session_id, payload=session.to_dict())
            return session

    def upsert(
        self,
        *,
        user_id: str,
        session_id: str,
        skill_name: str,
        tool_name: str | None,
        status: str,
        inputs: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        run_id: str | None = None,
        interaction_mode: str = "deterministic",
        expires_at: str | None = None,
    ) -> SkillSessionState:
        session = SkillSessionState(
            user_id=user_id,
            session_id=session_id,
            skill_name=skill_name,
            tool_name=tool_name,
            status=status,
            inputs=dict(inputs or {}),
            state=dict(state or {}),
            updated_at=utc_now_iso(),
            run_id=run_id,
            interaction_mode=interaction_mode,
            expires_at=expires_at or build_state_expiry(self.ttl_hours),
        )
        return self.save(session)

    def clear(self, *, user_id: str, session_id: str) -> bool:
        if self.state_repository is None:
            return self.file_store.clear(user_id=user_id, session_id=session_id)

        cleared = False
        try:
            cleared = self.state_repository.clear_skill_session_state(user_id=user_id, session_id=session_id)
        except DailyAgentError:
            pass
        file_cleared = self.file_store.clear(user_id=user_id, session_id=session_id)
        return cleared or file_cleared

    def _get_from_file(self, *, user_id: str, session_id: str) -> SkillSessionState | None:
        try:
            raw = self.file_store.get(user_id=user_id, session_id=session_id)
        except DailyAgentError:
            return None
        if not raw:
            return None
        return SkillSessionState(
            user_id=str(raw.get("user_id") or user_id),
            session_id=str(raw.get("session_id") or session_id),
            skill_name=str(raw.get("skill_name") or ""),
            tool_name=str(raw.get("tool_name")) if raw.get("tool_name") is not None else None,
            status=str(raw.get("status") or "pending"),
            inputs=dict(raw.get("inputs") or {}),
            state=dict(raw.get("state") or {}),
            updated_at=str(raw.get("updated_at") or utc_now_iso()),
            run_id=str(raw.get("run_id")) if raw.get("run_id") is not None else None,
            interaction_mode=str(raw.get("interaction_mode") or "deterministic"),
            expires_at=str(raw.get("expires_at")) if raw.get("expires_at") is not None else None,
        )


def _state_from_record(record: SkillSessionStateRecord) -> SkillSessionState:
    return SkillSessionState(
        user_id=record.user_id,
        session_id=record.session_id,
        skill_name=record.skill_name,
        tool_name=record.tool_name,
        status=record.status,
        inputs=record.inputs,
        state=record.state,
        updated_at=record.updated_at,
        run_id=record.run_id,
        interaction_mode=record.interaction_mode,
        expires_at=record.expires_at,
    )
