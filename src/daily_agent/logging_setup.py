from __future__ import annotations

import contextvars
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from daily_agent.paths import AppPaths
from daily_agent.config import DEFAULT_LOG_DIR, load_logging_settings

DEFAULT_LOGGER_NAME = "daily_agent"
DEFAULT_PROCESS_ROLE = "app"
DEFAULT_LOG_RETENTION_DAYS = 7

_CURRENT_RUN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("daily_agent_run_id", default="-")
_CURRENT_USER_ID: contextvars.ContextVar[str] = contextvars.ContextVar("daily_agent_user_id", default="-")
_CURRENT_SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("daily_agent_session_id", default="-")
_CURRENT_PROCESS_ROLE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "daily_agent_process_role",
    default=DEFAULT_PROCESS_ROLE,
)
_INITIALIZED_ROLES: set[str] = set()
_CONSOLE_READY = False


class DailyAgentContextFilter(logging.Filter):
    def __init__(self, *, only_role: str | None = None) -> None:
        super().__init__()
        self.only_role = only_role

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "run_id", None):
            record.run_id = _CURRENT_RUN_ID.get()
        if not getattr(record, "user_id", None):
            record.user_id = _CURRENT_USER_ID.get()
        if not getattr(record, "session_id", None):
            record.session_id = _CURRENT_SESSION_ID.get()
        if not getattr(record, "process_role", None):
            record.process_role = _CURRENT_PROCESS_ROLE.get()
        if self.only_role and record.process_role != self.only_role:
            return False
        return True


def setup_logging(*, process_role: str = DEFAULT_PROCESS_ROLE, paths: AppPaths | None = None) -> Path:
    global _CONSOLE_READY
    role = (process_role or DEFAULT_PROCESS_ROLE).strip() or DEFAULT_PROCESS_ROLE
    settings = load_logging_settings(paths=paths)
    log_dir = settings.directory or DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{role}.log"

    root_logger = logging.getLogger()
    logger = logging.getLogger(DEFAULT_LOGGER_NAME)

    formatter = logging.Formatter(
        fmt=(
            "%(asctime)s %(levelname)s %(name)s "
            "[role=%(process_role)s run_id=%(run_id)s user_id=%(user_id)s session_id=%(session_id)s] "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not _CONSOLE_READY:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.addFilter(DailyAgentContextFilter())
        console_handler.set_name("daily_agent_console")
        root_logger.addHandler(console_handler)
        _CONSOLE_READY = True

    if role not in _INITIALIZED_ROLES:
        file_handler = TimedRotatingFileHandler(
            filename=str(log_path),
            when="midnight",
            interval=1,
            backupCount=DEFAULT_LOG_RETENTION_DAYS,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(DailyAgentContextFilter(only_role=role))
        file_handler.set_name(f"daily_agent_file_{role}")
        root_logger.addHandler(file_handler)
        _INITIALIZED_ROLES.add(role)

    log_level = _resolve_log_level(settings.level)
    root_logger.setLevel(log_level)
    logger.setLevel(log_level)
    bind_log_context(process_role=role)
    return log_path


def bind_log_context(
    *,
    run_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    process_role: str | None = None,
) -> None:
    if run_id is not None:
        _CURRENT_RUN_ID.set(str(run_id) if str(run_id).strip() else "-")
    if user_id is not None:
        _CURRENT_USER_ID.set(str(user_id) if str(user_id).strip() else "-")
    if session_id is not None:
        _CURRENT_SESSION_ID.set(str(session_id) if str(session_id).strip() else "-")
    if process_role is not None:
        _CURRENT_PROCESS_ROLE.set(str(process_role) if str(process_role).strip() else DEFAULT_PROCESS_ROLE)


def clear_log_context() -> None:
    _CURRENT_RUN_ID.set("-")
    _CURRENT_USER_ID.set("-")
    _CURRENT_SESSION_ID.set("-")


def get_current_log_context() -> dict[str, str]:
    return {
        "run_id": _CURRENT_RUN_ID.get(),
        "user_id": _CURRENT_USER_ID.get(),
        "session_id": _CURRENT_SESSION_ID.get(),
        "process_role": _CURRENT_PROCESS_ROLE.get(),
    }


def log_level_name() -> str:
    return logging.getLevelName(logging.getLogger().level)


def reset_logging_state() -> None:
    global _CONSOLE_READY
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        handler_name = handler.get_name() or ""
        if handler_name == "daily_agent_console" or handler_name.startswith("daily_agent_file_"):
            root_logger.removeHandler(handler)
            handler.close()
    _INITIALIZED_ROLES.clear()
    _CONSOLE_READY = False
    clear_log_context()
    bind_log_context(process_role=DEFAULT_PROCESS_ROLE)


def _resolve_log_level(level_name: str) -> int:
    normalized = str(level_name or os.getenv("SB_AGENT_LOG_LEVEL") or os.getenv("DAILY_AGENT_LOG_LEVEL") or "INFO").strip().upper()
    return getattr(logging, normalized, logging.INFO)
