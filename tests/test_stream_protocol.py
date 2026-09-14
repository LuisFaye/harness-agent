import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from daily_agent.stream_protocol import StreamEventSequencer


def test_stream_event_sequencer_builds_v2_ndjson():
    sequencer = StreamEventSequencer("ask-001")

    first = json.loads(sequencer.serialize("run_started", {"status": "running"}))
    second = json.loads(sequencer.serialize("response_delta", {"delta": "你好\n"}))

    assert first == {
        "version": "2.0",
        "run_id": "ask-001",
        "sequence": 1,
        "type": "run_started",
        "timestamp": first["timestamp"],
        "payload": {"status": "running"},
    }
    assert second["sequence"] == 2
    assert second["payload"] == {"delta": "你好\n"}


def test_stream_event_sequences_are_thread_safe_and_unique():
    sequencer = StreamEventSequencer("ask-threaded")

    with ThreadPoolExecutor(max_workers=4) as pool:
        events = list(pool.map(lambda _: sequencer.next("response_delta", {"delta": "x"}), range(50)))

    assert sorted(event.sequence for event in events) == list(range(1, 51))
    assert sequencer.last_sequence == 50


def test_stream_event_rejects_unknown_type_and_invalid_payload():
    sequencer = StreamEventSequencer("ask-001")

    with pytest.raises(ValueError, match="未知的流式事件类型"):
        sequencer.next("unknown")
    with pytest.raises(ValueError, match="payload 必须是对象"):
        sequencer.next("run_started", ["错误类型"])
