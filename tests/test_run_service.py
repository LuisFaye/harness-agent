from __future__ import annotations

import json
from datetime import datetime, timezone

from daily_agent.run_repository import RunHistoryRecord, TaskHistoryRunRepository
from daily_agent.run_lifecycle_repository import RunLifecycleRecord
from daily_agent.services.run_service import RunService


class _RunRepositoryStub:
    def __init__(self, records=None, detail=None):
        self.records = records or []
        self.detail = detail
        self.list_calls = []
        self.detail_calls = []

    def list_records(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.records, len(self.records)

    def get_record(self, **kwargs):
        self.detail_calls.append(kwargs)
        return self.detail


class _LifecycleRepositoryStub:
    def __init__(self, records=None, detail=None):
        self.records = records or []
        self.detail = detail
        self.list_calls = []

    def list_unmirrored_runs(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.records, len(self.records)

    def get_run(self, **kwargs):
        return self.detail


def _record(metadata, *, status="success", output_summary="回答摘要"):
    return RunHistoryRecord(
        id=1,
        user_id="u1",
        user_input="请使用 sk-abcdefghijklmnop 检查项目",
        output_summary=output_summary,
        status=status,
        error_message=None,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if isinstance(metadata, dict) else metadata,
        created_at=datetime(2026, 7, 15, 6, 32, tzinfo=timezone.utc),
    )


def test_run_service_maps_complete_record_and_redacts_nested_secrets():
    metadata = {
        "run_id": "ask-20260715143200-123456",
        "session_id": "work",
        "response_text": "完成，调用地址 https://example.com?a=1&token=private-value",
        "plan": {"intent": "inspect", "steps": []},
        "started_at": "2026-07-15T14:31:48+08:00",
        "finished_at": "2026-07-15T14:32:00+08:00",
        "duration_ms": 12000,
        "execution_source": "agent_loop",
        "prompt_snapshot": {
            "model_id": "deepseek-chat",
            "authorization": "Bearer private-token",
            "prompt_text": "Bearer another-private-token",
        },
        "observations": [
            {
                "tool": "project_inspector",
                "inputs": {"access_token": "private", "path": "/tmp/demo"},
            }
        ],
        "trace": [{"stage": "tool", "details": {"password": "private"}}],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }
    repository = _RunRepositoryStub(detail=_record(metadata))

    detail = RunService(repository=repository).get_run(user_id="u1", run_id=metadata["run_id"])

    assert detail is not None
    assert detail.run_id == metadata["run_id"]
    assert detail.model_id == "deepseek-chat"
    assert detail.query == "请使用 ****** 检查项目"
    assert "token=******" in detail.response_text
    assert detail.prompt_snapshot["authorization"] == "******"
    assert detail.prompt_snapshot["prompt_text"] == "Bearer ******"
    assert detail.observations[0]["inputs"]["access_token"] == "******"
    assert detail.trace[0]["details"]["password"] == "******"
    assert detail.usage.total_tokens == 30
    assert detail.legacy_record is False


def test_run_service_degrades_invalid_legacy_metadata_without_crashing():
    record = _record("not-json", output_summary="旧回答摘要")
    repository = _RunRepositoryStub(records=[record], detail=record)
    service = RunService(repository=repository)

    page = service.list_runs(user_id="u1", keyword=" test ", limit=20, offset=0)
    detail = service.get_run(user_id="u1", run_id="legacy")

    assert page.items[0].run_id == "unknown"
    assert page.items[0].legacy_record is True
    assert detail is not None
    assert detail.response_text == "旧回答摘要"
    assert detail.plan is None
    assert detail.trace == []
    assert repository.list_calls[0]["keyword"] == "test"


def test_run_service_reports_truncated_field_paths():
    metadata = {
        "run_id": "ask-long",
        "response_text": "ok",
        "plan": {},
        "started_at": "2026-07-15T14:31:48+08:00",
        "finished_at": "2026-07-15T14:32:00+08:00",
        "duration_ms": 12000,
        "observations": [{"inputs": {"content": "x" * 2100}}],
    }
    detail = RunService(repository=_RunRepositoryStub(detail=_record(metadata))).get_run(
        user_id="u1",
        run_id="ask-long",
    )

    assert detail is not None
    assert "observations[0].inputs.content" in detail.truncated_fields
    assert detail.observations[0]["inputs"]["content"].endswith("...[内容过长，已截断]")


def test_run_service_merges_unmirrored_physical_runs_and_prefers_physical_detail():
    legacy_record = _record({"run_id": "ask-old", "response_text": "旧回答"})
    physical_record = RunLifecycleRecord(
        run_id="ask-running",
        user_id="u1",
        session_id="s1",
        status="running",
        query_text="正在检查项目",
        response_text=None,
        execution_source="agent_loop",
        model_id="deepseek-chat",
        error_stage=None,
        error_message=None,
        duration_ms=None,
        metadata_json=None,
        started_at=datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc),
        finished_at=None,
    )
    legacy_repository = _RunRepositoryStub(records=[legacy_record], detail=None)
    lifecycle_repository = _LifecycleRepositoryStub(records=[physical_record], detail=physical_record)
    service = RunService(
        repository=legacy_repository,
        lifecycle_repository=lifecycle_repository,
    )

    page = service.list_runs(user_id="u1", limit=20, offset=0)
    detail = service.get_run(user_id="u1", run_id="ask-running")

    assert page.total == 2
    assert [item.run_id for item in page.items] == ["ask-running", "ask-old"]
    assert detail is not None
    assert detail.status == "running"
    assert detail.session_id == "s1"
    assert legacy_repository.detail_calls == []


class _FakeCursor:
    def __init__(self):
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def fetchone(self):
        return {"total": 1}

    def fetchall(self):
        return [
            {
                "id": 1,
                "user_id": "u1",
                "user_input": "百分号 % 和下划线 _",
                "output_summary": "ok",
                "status": "success",
                "error_message": None,
                "metadata_json": '{"run_id":"ask-1"}',
                "created_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
            }
        ]


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return self._cursor


class _FakeStore:
    def __init__(self, connection):
        self.connection = connection

    def _connect(self):
        return self.connection


def test_run_repository_escapes_like_wildcards_and_paginates():
    cursor = _FakeCursor()
    repository = TaskHistoryRunRepository(store_factory=lambda: _FakeStore(_FakeConnection(cursor)))

    rows, total = repository.list_records(
        user_id="u1",
        status="success",
        session_id="work",
        keyword="%_",
        limit=20,
        offset=40,
    )

    assert total == 1
    assert rows[0].metadata_json == '{"run_id":"ask-1"}'
    assert cursor.executions[0][1] == ("u1", "success", "work", "%=%=_%", "%=%=_%")
    assert cursor.executions[1][1][-2:] == (20, 40)
    assert "ESCAPE '='" in cursor.executions[1][0]
