from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from daily_agent.run_models import RunDetail, RunListResponse, RunSummary, RunUsage
from daily_agent.citations.models import CitationBundle
from daily_agent.model_request_store import load_model_requests
from daily_agent.run_lifecycle_repository import MySQLRunLifecycleRepository, RunLifecycleRecord
from daily_agent.run_repository import RunHistoryRecord, TaskHistoryRunRepository

_SECRET_FIELD_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "cookie",
}
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+")
_SK_PATTERN = re.compile(r"(?i)\bsk-[a-z0-9._-]{6,}")
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:api_?key|token|access_token|secret|password)=)[^&#\s]+"
)


class RunService:
    """把 task_history 的内部 JSON 映射成稳定、脱敏的运行监控模型。"""

    def __init__(
        self,
        repository: TaskHistoryRunRepository | None = None,
        lifecycle_repository: MySQLRunLifecycleRepository | None = None,
    ) -> None:
        self.repository = repository or TaskHistoryRunRepository()
        # 单元测试显式注入旧仓储时保持原行为；生产默认同时读取物理运行表。
        self.lifecycle_repository = lifecycle_repository if lifecycle_repository is not None else (
            None if repository is not None else MySQLRunLifecycleRepository()
        )

    def list_runs(
        self,
        *,
        user_id: str,
        status: str | None = None,
        session_id: str | None = None,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> RunListResponse:
        cleaned_session_id = _clean_optional_text(session_id)
        cleaned_keyword = _clean_optional_text(keyword)
        if self.lifecycle_repository is None:
            records, total = self.repository.list_records(
                user_id=user_id,
                status=status,
                session_id=cleaned_session_id,
                keyword=cleaned_keyword,
                limit=limit,
                offset=offset,
            )
            return RunListResponse(
                items=[self._to_summary(record) for record in records],
                total=total,
                limit=limit,
                offset=offset,
            )

        window_size = offset + limit
        legacy_records, legacy_total = self.repository.list_records(
            user_id=user_id,
            status=status,
            session_id=cleaned_session_id,
            keyword=cleaned_keyword,
            limit=window_size,
            offset=0,
        )
        physical_records, physical_total = self.lifecycle_repository.list_unmirrored_runs(
            user_id=user_id,
            status=status,
            session_id=cleaned_session_id,
            keyword=cleaned_keyword,
            limit=window_size,
        )
        records = [*legacy_records, *[_history_from_lifecycle(item) for item in physical_records]]
        records.sort(key=_history_sort_timestamp, reverse=True)
        page_records = records[offset:window_size]
        return RunListResponse(
            items=[self._to_summary(record) for record in page_records],
            total=legacy_total + physical_total,
            limit=limit,
            offset=offset,
        )

    def get_run(self, *, user_id: str, run_id: str) -> RunDetail | None:
        lifecycle_record = (
            self.lifecycle_repository.get_run(user_id=user_id, run_id=run_id)
            if self.lifecycle_repository is not None
            else None
        )
        record = _history_from_lifecycle(lifecycle_record) if lifecycle_record else None
        if record is None:
            record = self.repository.get_record(user_id=user_id, run_id=run_id)
        if record is None:
            return None
        metadata = _parse_metadata(record.metadata_json)
        summary = self._to_summary(record, metadata=metadata)
        truncated_fields: list[str] = []

        query = _sanitize_value(record.user_input, path="query", truncated_fields=truncated_fields)
        response_text = _sanitize_value(
            metadata.get("response_text") or record.output_summary or "",
            path="response_text",
            truncated_fields=truncated_fields,
        )
        plan = _sanitize_mapping(metadata.get("plan"), "plan", truncated_fields)
        observations = _sanitize_list(metadata.get("observations"), "observations", truncated_fields)
        trace = _sanitize_list(metadata.get("trace"), "trace", truncated_fields)
        prompt_snapshot = _sanitize_mapping(
            metadata.get("prompt_snapshot"),
            "prompt_snapshot",
            truncated_fields,
        )
        # 优先读本地短命存档；旧数据若仍残留在 metadata 里则作为一次性回退。
        model_requests = _sanitize_list(
            load_model_requests(run_id),
            "model_requests",
            truncated_fields,
        )
        if not model_requests:
            model_requests = _sanitize_list(
                metadata.get("model_requests"),
                "model_requests",
                truncated_fields,
            )
        working_state = _sanitize_mapping(
            metadata.get("working_state"),
            "working_state",
            truncated_fields,
        )
        missing_info = [
            str(_sanitize_value(item, path="missing_info", truncated_fields=truncated_fields))
            for item in _as_list(metadata.get("missing_info"))
        ]
        error_message = _optional_string(
            _sanitize_value(record.error_message, path="error_message", truncated_fields=truncated_fields)
        )
        citation_bundle = _citation_bundle_from_metadata(
            metadata,
            truncated_fields=truncated_fields,
        )

        return RunDetail(
            **summary.model_dump(),
            query=str(query or ""),
            response_text=str(response_text or ""),
            stop_reason=_optional_string(
                _sanitize_value(metadata.get("stop_reason"), path="stop_reason", truncated_fields=truncated_fields)
            ),
            missing_info=missing_info,
            plan=plan,
            observations=observations,
            trace=trace,
            prompt_snapshot=prompt_snapshot,
            model_requests=model_requests or [],
            working_state=working_state,
            usage=_usage_from_metadata(metadata),
            error_message=error_message,
            truncated_fields=sorted(set(truncated_fields)),
            citation_bundle=citation_bundle,
        )

    def _to_summary(
        self,
        record: RunHistoryRecord,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> RunSummary:
        metadata = metadata if metadata is not None else _parse_metadata(record.metadata_json)
        prompt_snapshot = metadata.get("prompt_snapshot") if isinstance(metadata.get("prompt_snapshot"), dict) else {}
        response_text = str(metadata.get("response_text") or record.output_summary or "")
        query_summary = _compact_text(str(_sanitize_value(record.user_input, path="query_summary") or ""), 160)
        response_summary = _compact_text(str(_sanitize_value(record.output_summary or response_text, path="response_summary") or ""), 240)
        usage = _usage_from_metadata(metadata)
        finished_at = _parse_datetime(metadata.get("finished_at")) or record.created_at
        started_at = _parse_datetime(metadata.get("started_at")) or finished_at
        legacy_record = not all(
            key in metadata for key in ("response_text", "plan", "started_at", "finished_at", "duration_ms")
        )
        return RunSummary(
            run_id=str(metadata.get("run_id") or "unknown"),
            user_id=record.user_id,
            session_id=str(metadata.get("session_id") or prompt_snapshot.get("session_id") or "unknown"),
            status=record.status or "unknown",
            query_summary=query_summary,
            response_summary=response_summary,
            execution_source=str(metadata.get("execution_source") or "unknown"),
            model_id=_optional_string(metadata.get("model_id") or prompt_snapshot.get("model_id")),
            error_stage=_optional_string(metadata.get("error_stage")),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=_as_non_negative_int(metadata.get("duration_ms")),
            total_tokens=usage.total_tokens,
            has_detail=bool(metadata),
            legacy_record=legacy_record,
        )


def _parse_metadata(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _usage_from_metadata(metadata: dict[str, Any]) -> RunUsage:
    usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
    return RunUsage(
        input_tokens=_as_non_negative_int(usage.get("input_tokens")),
        output_tokens=_as_non_negative_int(usage.get("output_tokens")),
        total_tokens=_as_non_negative_int(usage.get("total_tokens")),
        cache_read_tokens=_as_non_negative_int(usage.get("cache_read_tokens")),
        cache_miss_tokens=_as_non_negative_int(usage.get("cache_miss_tokens")),
        cache_write_tokens=_as_non_negative_int(usage.get("cache_write_tokens")),
        reasoning_tokens=_as_non_negative_int(usage.get("reasoning_tokens")),
    )


def _citation_bundle_from_metadata(
    metadata: dict[str, Any],
    *,
    truncated_fields: list[str],
) -> CitationBundle | None:
    raw = _sanitize_mapping(metadata.get("citation_bundle"), "citation_bundle", truncated_fields)
    if raw is None:
        return None
    try:
        return CitationBundle.model_validate(raw)
    except ValueError:
        # 历史元数据或手工写入数据不满足当前协议时，详情接口只忽略引用，
        # 不能让一条旧记录导致整个运行详情返回 500。
        return None


def _sanitize_mapping(
    value: Any,
    path: str,
    truncated_fields: list[str],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    sanitized = _sanitize_value(value, path=path, truncated_fields=truncated_fields)
    return sanitized if isinstance(sanitized, dict) else None


def _sanitize_list(
    value: Any,
    path: str,
    truncated_fields: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sanitized = _sanitize_value(value, path=path, truncated_fields=truncated_fields)
    if not isinstance(sanitized, list):
        return []
    return [item for item in sanitized if isinstance(item, dict)]


def _sanitize_value(
    value: Any,
    *,
    path: str,
    truncated_fields: list[str] | None = None,
    depth: int = 0,
) -> Any:
    truncated_fields = truncated_fields if truncated_fields is not None else []
    if depth > 12:
        truncated_fields.append(path)
        return "...[内容层级过深，已截断]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if _is_secret_field(key_text):
                sanitized[key_text] = "******"
            else:
                sanitized[key_text] = _sanitize_value(
                    item,
                    path=child_path,
                    truncated_fields=truncated_fields,
                    depth=depth + 1,
                )
        return sanitized
    if isinstance(value, list):
        items = value[:200]
        sanitized_items = [
            _sanitize_value(
                item,
                path=f"{path}[{index}]",
                truncated_fields=truncated_fields,
                depth=depth + 1,
            )
            for index, item in enumerate(items)
        ]
        if len(value) > len(items):
            truncated_fields.append(path)
            sanitized_items.append("...[列表过长，已截断]")
        return sanitized_items
    if isinstance(value, str):
        redacted = _redact_text(value)
        max_length = _max_string_length(path)
        if len(redacted) > max_length:
            truncated_fields.append(path)
            return f"{redacted[:max_length]}\n...[内容过长，已截断]"
        return redacted
    return value


def sanitize_run_value(value: Any, *, path: str = "value") -> Any:
    """供运行详情和事件接口共用的递归脱敏入口。"""
    return _sanitize_value(value, path=path)


def _is_secret_field(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    return any(
        normalized == name
        or normalized.startswith(f"{name}_")
        or normalized.endswith(f"_{name}")
        for name in _SECRET_FIELD_NAMES
    )


def _redact_text(value: str) -> str:
    redacted = _BEARER_PATTERN.sub(r"\1******", value)
    redacted = _SK_PATTERN.sub("******", redacted)
    return _QUERY_SECRET_PATTERN.sub(r"\1******", redacted)


def _max_string_length(path: str) -> int:
    lowered = path.lower()
    if "model_requests" in lowered:
        return 100000
    if "prompt_snapshot" in lowered:
        return 50000
    if "error" in lowered:
        return 12000
    if "trace" in lowered:
        return 8000
    if "observations" in lowered and "input" in lowered:
        return 2000
    if "observations" in lowered and "output" in lowered:
        return 4000
    return 8000


def _compact_text(value: str, max_chars: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars - 1]}…"


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_non_negative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _history_from_lifecycle(record: RunLifecycleRecord) -> RunHistoryRecord:
    metadata = _parse_metadata(record.metadata_json)
    metadata.setdefault("run_id", record.run_id)
    metadata.setdefault("session_id", record.session_id)
    metadata.setdefault("response_text", record.response_text or "")
    metadata.setdefault("execution_source", record.execution_source or "unknown")
    metadata.setdefault("model_id", record.model_id)
    metadata.setdefault("error_stage", record.error_stage)
    metadata.setdefault("duration_ms", record.duration_ms)
    metadata.setdefault("started_at", record.started_at.isoformat() if record.started_at else None)
    metadata.setdefault("finished_at", record.finished_at.isoformat() if record.finished_at else None)
    return RunHistoryRecord(
        id=0,
        user_id=record.user_id,
        user_input=record.query_text,
        output_summary=record.response_text,
        status=record.status,
        error_message=record.error_message,
        metadata_json=metadata,
        created_at=record.started_at,
    )


def _history_sort_timestamp(record: RunHistoryRecord) -> float:
    value = record.created_at
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()
