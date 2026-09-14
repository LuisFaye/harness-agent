from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Preference:
    key: str
    value: str
    updated_at: datetime | None = None


@dataclass(frozen=True)
class TaskHistory:
    id: int
    task_type: str
    user_input: str
    output_summary: str | None
    status: str
    error_message: str | None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ToolCallLog:
    id: int
    tool_name: str
    input_json: str | None
    output_summary: str | None
    status: str
    error_message: str | None
    duration_ms: int
    created_at: datetime | None = None


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    memory_text: str
    source: str
    created_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ConversationMessage:
    id: int
    session_id: str
    role: str
    content: str
    created_at: datetime | None = None
    run_id: str | None = None
    attachment_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversationSummary:
    user_id: str
    session_id: str
    summary_text: str
    summarized_message_count: int
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ConversationSessionRecord:
    """工作台会话列表使用的轻量摘要，不包含完整消息正文。"""

    session_id: str
    title: str
    preview: str
    message_count: int
    last_message_at: datetime | None = None
    has_summary: bool = False


@dataclass(frozen=True)
class MemoryDeduplicationResult:
    scanned_count: int
    deleted_count: int
    kept_ids: list[int]
    deleted_ids: list[int]


@dataclass(frozen=True)
class ConversationCleanupResult:
    user_id: str
    session_id: str | None
    older_than_days: int
    cutoff_at: datetime
    deleted_messages: int
    deleted_summaries: int
    affected_sessions: list[str]


@dataclass(frozen=True)
class ScheduledJob:
    """定时任务定义，保存调度时间、业务配置和最近运行状态。"""

    id: int
    user_id: str
    job_type: str
    title: str
    status: str
    schedule_kind: str
    schedule_time: str
    timezone: str
    config_json: str | None
    next_run_at: datetime
    last_run_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ScheduledJobRun:
    """定时任务的一次执行记录。"""

    id: int
    job_id: int
    user_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    output_text: str | None
    error_message: str | None
    metadata_json: str | None
    created_at: datetime | None = None
