from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from daily_agent.memory_models import MemoryRecord, ScheduledJob, ScheduledJobRun

DEFAULT_USER_ID = "default"
DEFAULT_SESSION_ID = "default"
DEFAULT_CONVERSATION_TURN_LIMIT = 3
DEFAULT_CONVERSATION_COMPACTION_TOKEN_THRESHOLD = 24_000
DEFAULT_CONVERSATION_EPOCH_TAIL_TURNS = 8
# wheel/容器从包内读取 SQL；源码和 editable 安装保留仓库根目录回退，兼容本地开发。
_PACKAGED_SCHEMA_PATH = Path(__file__).resolve().parent / "sql" / "mysql_memory.sql"
_SOURCE_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "mysql_memory.sql"
SCHEMA_PATH = _PACKAGED_SCHEMA_PATH if _PACKAGED_SCHEMA_PATH.exists() else _SOURCE_SCHEMA_PATH


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_memory_text(text: str) -> str:
    # 用户可能用不同数量的空格、换行保存同一条记忆，归一化后才能可靠去重。
    return " ".join(text.split())


def normalize_conversation_text(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_attachment_ids(attachment_ids: list[str] | None) -> tuple[str, ...]:
    """去重并限制用户消息可附带的附件数量。"""
    if not attachment_ids:
        return ()
    normalized: list[str] = []
    for attachment_id in attachment_ids:
        value = str(attachment_id or "").strip()
        if value and value not in normalized:
            normalized.append(value)
        if len(normalized) >= 10:
            break
    return tuple(normalized)


def parse_attachment_ids(raw: Any) -> tuple[str, ...]:
    """解析 conversation_messages.attachment_ids 字段。"""
    if raw in (None, ""):
        return ()
    if isinstance(raw, (list, tuple)):
        return normalize_attachment_ids(list(raw))
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return ()
        if isinstance(payload, list):
            return normalize_attachment_ids(payload)
    return ()


def normalize_session_id(session_id: str) -> str:
    # 会话 ID 既允许手工填写也允许使用默认值，去掉两端空白可避免产生重复会话。
    return session_id.strip() or DEFAULT_SESSION_ID


def summarize_text_for_title(text: str, max_chars: int = 36) -> str:
    """把会话标题压成适合侧边栏显示的一行文本。"""
    compact = " ".join(text.split())
    return compact[:max_chars] or DEFAULT_SESSION_ID


def summarize_text_for_preview(text: str, max_chars: int = 60) -> str:
    """把会话预览压成适合侧边栏显示的短句。"""
    compact = " ".join(text.split())
    return compact[:max_chars]


def build_tool_log_input_payload(
    *,
    inputs: dict[str, Any],
    run_id: str | None,
    source_skill: str | None,
    retry_count: int | None,
    risk_level: str | None,
    requires_confirmation: bool | None,
    confirmed: bool | None,
    failure_stage: str | None,
) -> dict[str, Any]:
    metadata = {
        "run_id": run_id,
        "source_skill": source_skill,
        "retry_count": retry_count,
        "risk_level": risk_level,
        "requires_confirmation": requires_confirmation,
        "confirmed": confirmed,
        "failure_stage": failure_stage,
    }
    compact_metadata = {key: value for key, value in metadata.items() if value is not None}
    if not compact_metadata:
        return inputs
    return {"inputs": inputs, "metadata": compact_metadata}


def memory_record_from_row(row: dict[str, Any]) -> MemoryRecord:
    return MemoryRecord(
        id=int(row["id"]),
        memory_text=row["memory_text"],
        source=row["source"],
        created_at=row["created_at"],
        expires_at=row.get("expires_at"),
    )


def scheduled_job_from_row(row: dict[str, Any]) -> ScheduledJob:
    """把数据库查询结果转换成 ScheduledJob。"""
    return ScheduledJob(
        id=int(row["id"]),
        user_id=row["user_id"],
        job_type=row["job_type"],
        title=row["title"],
        status=row["status"],
        schedule_kind=row["schedule_kind"],
        schedule_time=row["schedule_time"],
        timezone=row["timezone"],
        config_json=row["config_json"],
        last_run_at=row["last_run_at"],
        next_run_at=row["next_run_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def scheduled_job_run_from_row(row: dict[str, Any]) -> ScheduledJobRun:
    """把数据库查询结果转换成 ScheduledJobRun。"""
    return ScheduledJobRun(
        id=int(row["id"]),
        job_id=int(row["job_id"]),
        user_id=row["user_id"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        output_text=row["output_text"],
        error_message=row["error_message"],
        metadata_json=row["metadata_json"],
        created_at=row["created_at"],
    )


def load_schema_statements(path: Path = SCHEMA_PATH) -> list[str]:
    """读取建表 SQL，并按分号拆成可逐条执行的语句。"""
    sql = path.read_text(encoding="utf-8")
    statements = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip(";").strip())
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return statements
