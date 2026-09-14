from __future__ import annotations

import json
from pathlib import Path
import re

from daily_agent.run_models import RunEventListResponse
from daily_agent.stream_protocol import STREAM_EVENT_TYPES, STREAM_PROTOCOL_VERSION


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "frontend" / "src" / "test" / "fixtures"
STREAM_FIXTURES = (
    "stream-success.ndjson",
    "stream-failed.ndjson",
    "stream-cancelled.ndjson",
    "stream-waiting-confirmation.ndjson",
    "stream-recovery-initial.ndjson",
)


def _read_ndjson_fixture(name: str) -> list[dict]:
    """读取脱敏后的前端契约夹具，空行不参与事件序列。"""
    return [
        json.loads(line)
        for line in (FIXTURE_DIR / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_frontend_stream_fixtures_follow_backend_envelope_and_sequence():
    for name in STREAM_FIXTURES:
        events = _read_ndjson_fixture(name)
        assert events
        assert {event["version"] for event in events} == {STREAM_PROTOCOL_VERSION}
        assert {event["type"] for event in events}.issubset(STREAM_EVENT_TYPES)
        assert len({event["run_id"] for event in events}) == 1
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert all(isinstance(event["payload"], dict) for event in events)


def test_frontend_recovery_fixture_matches_backend_response_model():
    payload = json.loads(
        (FIXTURE_DIR / "stream-recovery-page.json").read_text(encoding="utf-8")
    )
    page = RunEventListResponse.model_validate(payload)

    assert page.run_id == "ask-fixture-recovery"
    assert page.last_sequence == 6
    assert [event.sequence for event in page.events] == [4, 5, 6]
    assert page.has_more is False


def test_frontend_fixtures_do_not_contain_sensitive_configuration():
    fixture_text = "\n".join(
        (FIXTURE_DIR / name).read_text(encoding="utf-8")
        for name in (*STREAM_FIXTURES, "stream-recovery-page.json")
    ).lower()

    assert "api_key" not in fixture_text
    assert "access_token" not in fixture_text
    assert re.search(r"\bsk-[a-z0-9_-]{8,}", fixture_text) is None
