from datetime import datetime, timezone

from daily_agent.run_lifecycle_repository import (
    MySQLRunLifecycleRepository,
    RunLifecycleRecord,
    StoredRunEvent,
)
from daily_agent.services.run_lifecycle_service import RunLifecycleService
from daily_agent.stream_protocol import StreamEventSequencer


class _RepositoryStub:
    def __init__(self):
        self.create_calls = []
        self.append_calls = []
        self.finalize_calls = []
        self.run = None
        self.events = []
        self.has_more = False
        self.cancel_status = None
        self.cancel_changed = False

    def create_run(self, **kwargs):
        self.create_calls.append(kwargs)

    def append_event(self, event):
        self.append_calls.append(event)

    def append_events(self, events):
        self.append_calls.extend(events)

    def finalize_run(self, **kwargs):
        self.finalize_calls.append(kwargs)

    def get_run(self, **kwargs):
        return self.run

    def list_events(self, **kwargs):
        return self.events, self.has_more

    def request_cancel(self, **kwargs):
        return self.cancel_status, self.cancel_changed


class _LegacyRepositoryStub:
    def __init__(self, record=None):
        self.record = record

    def get_record(self, **kwargs):
        return self.record


def test_run_lifecycle_service_maps_result_and_events():
    repository = _RepositoryStub()
    service = RunLifecycleService(repository=repository)
    sequencer = StreamEventSequencer("ask-001")
    terminal_event = sequencer.next("run_completed", {"status": "success"})
    result = {
        "run_id": "ask-001",
        "status": "success",
        "response_text": "完成",
        "execution_source": "agent_loop",
        "error_stage": None,
        "stop_reason": "任务完成",
        "prompt_snapshot": {"model_id": "deepseek-chat"},
    }

    service.start_run(run_id="ask-001", user_id="u1", session_id="s1", query_text="检查项目")
    service.record_event(sequencer.next("response_completed", {"text": "完成"}))
    service.complete_run(
        run_id="ask-001",
        user_id="u1",
        result=result,
        terminal_event=terminal_event,
    )

    assert repository.create_calls[0]["status"] == "running"
    assert repository.append_calls[0].type == "response_completed"
    assert repository.finalize_calls[0]["model_id"] == "deepseek-chat"
    assert repository.finalize_calls[0]["terminal_event"] == terminal_event
    assert repository.finalize_calls[0]["error_message"] is None


def test_run_lifecycle_service_lists_events_after_ownership_check():
    repository = _RepositoryStub()
    repository.run = RunLifecycleRecord(
        run_id="ask-001",
        user_id="u1",
        session_id="s1",
        status="running",
        query_text="检查",
        response_text=None,
        execution_source=None,
        model_id=None,
        error_stage=None,
        error_message=None,
        duration_ms=None,
        metadata_json=None,
        started_at=None,
        finished_at=None,
    )
    repository.events = [
        StoredRunEvent(
            run_id="ask-001",
            sequence=4,
            event_type="response_delta",
            event_timestamp="2026-07-15T18:00:00+08:00",
            payload={"delta": "A", "api_key": "sk-secret-value"},
        )
    ]
    service = RunLifecycleService(repository=repository)

    page = service.list_events(
        user_id="u1",
        run_id="ask-001",
        after_sequence=3,
        limit=20,
    )

    assert page is not None
    assert page.last_sequence == 4
    assert page.events[0].payload == {"delta": "A", "api_key": "******"}


def test_run_lifecycle_service_returns_empty_event_page_for_legacy_run():
    repository = _RepositoryStub()
    legacy_repository = _LegacyRepositoryStub(record=object())
    service = RunLifecycleService(
        repository=repository,
        legacy_repository=legacy_repository,
    )

    page = service.list_events(
        user_id="u1",
        run_id="ask-legacy",
        after_sequence=7,
        limit=20,
    )

    assert page is not None
    assert page.events == []
    assert page.last_sequence == 7
    assert page.has_more is False


def test_run_lifecycle_service_cancels_active_run_and_writes_cancelled_terminal(monkeypatch):
    repository = _RepositoryStub()
    repository.cancel_status = "cancel_requested"
    repository.cancel_changed = True
    service = RunLifecycleService(repository=repository)
    notifications = []
    monkeypatch.setattr(
        "daily_agent.services.run_lifecycle_service._cancel_agno_run",
        lambda run_id: run_id == "ask-001",
    )
    service.start_run(run_id="ask-001", user_id="u1", session_id="s1", query_text="检查")
    service.bind_cancel_notifier(run_id="ask-001", notifier=lambda: notifications.append("notified"))

    cancel_response = service.request_cancel(run_id="ask-001", user_id="u1")
    result, terminal_event = service.complete_run(
        run_id="ask-001",
        user_id="u1",
        result={"status": "success", "response_text": "原本会完成"},
        terminal_event_factory=lambda event_type, payload: StreamEventSequencer("ask-001").next(
            event_type,
            {"result": payload},
        ),
    )

    assert cancel_response is not None
    assert cancel_response.accepted is True
    assert cancel_response.executor_notified is True
    assert notifications == ["notified"]
    assert result["status"] == "cancelled"
    assert terminal_event.type == "run_cancelled"
    assert repository.finalize_calls[0]["status"] == "cancelled"


class _FakeCursor:
    def __init__(self):
        self.executions = []
        self.rowcount = 1
        self._last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params):
        self._last_sql = sql
        self.executions.append((sql, params))

    def fetchone(self):
        return {
            "run_id": "ask-001",
            "user_id": "u1",
            "session_id": "s1",
            "status": "success",
            "query_text": "检查项目",
            "response_text": "完成",
            "execution_source": "agent_loop",
            "model_id": "deepseek-chat",
            "error_stage": None,
            "error_message": None,
            "duration_ms": 100,
            "metadata_json": {"status": "success"},
            "started_at": datetime(2026, 7, 15),
            "finished_at": datetime(2026, 7, 15),
        }

    def fetchall(self):
        return [
            {
                "run_id": "ask-001",
                "sequence": 1,
                "event_type": "run_started",
                "event_timestamp": "2026-07-15T18:00:00+08:00",
                "payload_json": '{"status":"running"}',
            },
            {
                "run_id": "ask-001",
                "sequence": 2,
                "event_type": "response_completed",
                "event_timestamp": "2026-07-15T18:00:01+08:00",
                "payload_json": {"text": "完成"},
            },
        ]


class _CancelCursor(_FakeCursor):
    def __init__(self, status):
        super().__init__()
        self.status = status

    def fetchone(self):
        return {"status": self.status}


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commit_count += 1


class _FakeStore:
    def __init__(self, connection):
        self.connection = connection

    def _connect(self):
        return self.connection


def test_run_lifecycle_repository_writes_terminal_event_in_same_transaction():
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)
    repository = MySQLRunLifecycleRepository(store_factory=lambda: _FakeStore(connection))
    event = StreamEventSequencer("ask-001").next("run_completed", {"status": "success"})

    repository.finalize_run(
        run_id="ask-001",
        user_id="u1",
        status="success",
        response_text="完成",
        execution_source="agent_loop",
        model_id="deepseek-chat",
        error_stage=None,
        error_message=None,
        metadata={"status": "success"},
        terminal_event=event,
        finished_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert len(cursor.executions) == 2
    assert "UPDATE agent_runs" in cursor.executions[0][0]
    assert "INSERT INTO agent_run_events" in cursor.executions[1][0]
    assert cursor.executions[1][1][1] == 1
    assert connection.commit_count == 1


def test_run_lifecycle_repository_marks_running_run_cancel_requested():
    cursor = _CancelCursor("running")
    connection = _FakeConnection(cursor)
    repository = MySQLRunLifecycleRepository(store_factory=lambda: _FakeStore(connection))

    status, changed = repository.request_cancel(
        run_id="ask-001",
        user_id="u1",
        requested_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )

    assert status == "cancel_requested"
    assert changed is True
    assert "FOR UPDATE" in cursor.executions[0][0]
    assert "cancel_requested_at" in cursor.executions[1][0]
    assert connection.commit_count == 1


def test_run_lifecycle_repository_does_not_change_completed_run():
    cursor = _CancelCursor("success")
    connection = _FakeConnection(cursor)
    repository = MySQLRunLifecycleRepository(store_factory=lambda: _FakeStore(connection))

    status, changed = repository.request_cancel(run_id="ask-001", user_id="u1")

    assert status == "success"
    assert changed is False
    assert len(cursor.executions) == 1


def test_run_lifecycle_repository_reads_limit_plus_one_for_has_more():
    cursor = _FakeCursor()
    connection = _FakeConnection(cursor)
    repository = MySQLRunLifecycleRepository(store_factory=lambda: _FakeStore(connection))

    events, has_more = repository.list_events(run_id="ask-001", after_sequence=0, limit=1)

    assert has_more is True
    assert len(events) == 1
    assert events[0].payload == {"status": "running"}
    assert cursor.executions[0][1] == ("ask-001", 0, 2)
