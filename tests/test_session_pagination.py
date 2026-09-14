from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from daily_agent.api import create_app
from daily_agent.memory import MySQLMemoryStore, _literal_like_pattern
from daily_agent.services.platform_service import ConversationService


class _Cursor:
    def __init__(self, results):
        self.results = list(results)
        self.executions = []
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.executions.append((sql, params))
        self.current = self.results.pop(0)

    def fetchone(self):
        return self.current

    def fetchall(self):
        return self.current


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def test_session_store_uses_literal_keyword_and_fixed_batch_queries():
    timestamp = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    cursor = _Cursor([
        {"total": 2},
        [
            {
                "session_id": "s-1",
                "last_message_at": timestamp,
                "message_count": 4,
            },
            {
                "session_id": "s-2",
                "last_message_at": timestamp,
                "message_count": 2,
            },
        ],
        [
            {
                "session_id": "s-1",
                "role": "user",
                "content": "第一条问题",
                "latest_rank": 2,
                "first_role_rank": 1,
            },
            {
                "session_id": "s-1",
                "role": "assistant",
                "content": "最后回答",
                "latest_rank": 1,
                "first_role_rank": 1,
            },
            {
                "session_id": "s-2",
                "role": "assistant",
                "content": "只有回答",
                "latest_rank": 1,
                "first_role_rank": 1,
            },
        ],
        [{"session_id": "s-1"}],
    ])
    store = object.__new__(MySQLMemoryStore)

    @contextmanager
    def connect():
        yield _Connection(cursor)

    store._connect = connect
    rows, total = store.list_conversation_sessions(
        user_id="u-1",
        limit=30,
        offset=30,
        keyword=r"  100%_done\ok  ",
    )

    assert total == 2
    assert len(cursor.executions) == 4
    assert _literal_like_pattern(r"100%_done\ok") == r"%100\%\_done\\ok%"
    assert cursor.executions[0][1] == (
        "u-1",
        r"%100\%\_done\\ok%",
        r"%100\%\_done\\ok%",
    )
    assert cursor.executions[1][1][-2:] == (30, 30)
    assert "ESCAPE '\\\\'" in cursor.executions[0][0]
    assert [row.session_id for row in rows] == ["s-1", "s-2"]
    assert rows[0].title == "第一条问题"
    assert rows[0].preview == "最后回答"
    assert rows[0].has_summary is True
    assert rows[1].title == "只有回答"
    assert rows[1].has_summary is False


def test_session_service_returns_empty_page_when_message_table_is_missing(monkeypatch):
    service = ConversationService(store=SimpleNamespace())
    monkeypatch.setattr(service, "_table_available", lambda _table: False)

    assert service.list_sessions(limit=30, offset=60, keyword="报告") == {
        "items": [],
        "total": 0,
        "limit": 30,
        "offset": 60,
    }


class _ConversationServiceStub:
    def __init__(self):
        self.calls = []

    def list_sessions(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "items": [
                SimpleNamespace(
                    session_id="session-1",
                    title="第一条问题",
                    preview="最后回答",
                    message_count=4,
                    last_message_at=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
                    has_summary=True,
                )
            ],
            "total": 61,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
        }


def test_sessions_api_exposes_pagination_and_search_contract():
    service = _ConversationServiceStub()
    client = TestClient(create_app(conversation_service=service))

    response = client.get(
        "/sessions",
        params={
            "user_id": "u-1",
            "limit": 30,
            "offset": 30,
            "keyword": r"100%_done\ok",
        },
    )

    assert response.status_code == 200
    assert response.json()["total"] == 61
    assert response.json()["limit"] == 30
    assert response.json()["offset"] == 30
    assert response.json()["items"][0]["session_id"] == "session-1"
    assert service.calls == [{
        "user_id": "u-1",
        "limit": 30,
        "offset": 30,
        "keyword": r"100%_done\ok",
    }]
