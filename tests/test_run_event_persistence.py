from daily_agent.run_event_persistence import (
    BatchedRunEventWriter,
    compact_stream_event_for_storage,
    should_persist_stream_event,
)
from daily_agent.stream_protocol import StreamEventSequencer


def test_should_not_persist_high_frequency_stream_events():
    sequencer = StreamEventSequencer("ask-001")
    assert should_persist_stream_event(sequencer.next("response_delta", {"delta": "你"})) is False
    assert should_persist_stream_event(sequencer.next("status_updated", {"state": {"phase": "executing"}})) is False


def test_should_not_persist_reasoning_progress_trace():
    sequencer = StreamEventSequencer("ask-001")
    event = sequencer.next(
        "trace_updated",
        {
            "trace": {
                "stage": "reasoning",
                "status": "running",
                "key": "reasoning",
                "details": {"reasoning_content": "正在思考"},
            }
        },
    )
    assert should_persist_stream_event(event) is False


def test_compact_prompt_snapshot_for_storage():
    sequencer = StreamEventSequencer("ask-001")
    event = sequencer.next(
        "trace_updated",
        {
            "trace": {
                "stage": "prompt_ready",
                "status": "success",
                "details": {
                    "prompt_snapshot": {
                        "run_id": "ask-001",
                        "model_id": "deepseek-v4-flash",
                        "context_token_budget": 1_000_000,
                        "instructions": ["a"] * 50,
                        "tool_names": ["t1", "t2"],
                        "skill_names": ["s1"],
                    }
                },
            }
        },
    )
    compacted = compact_stream_event_for_storage(event)
    snapshot = compacted.payload["trace"]["details"]["prompt_snapshot"]
    assert snapshot["instruction_count"] == 50
    assert snapshot["tool_count"] == 2
    assert snapshot["storage_compact"] is True
    assert "instructions" not in snapshot


def test_batched_writer_flushes_on_shutdown():
    batches: list[list] = []
    writer = BatchedRunEventWriter(lambda items: batches.append(list(items)), batch_size=10)
    sequencer = StreamEventSequencer("ask-002")
    writer.schedule(sequencer.next("run_started", {"status": "running"}))
    writer.schedule(sequencer.next("response_completed", {"text": "完成"}))
    writer.flush()
    writer.shutdown()
    assert batches
    assert sum(len(batch) for batch in batches) == 2
