"""运行流式事件的落库策略与异步批量写入。

热路径只负责把事件推给前端；需要持久化的事件进入后台队列，按批 INSERT，
避免每个 token / reasoning 增量都触发一次 commit。同时过滤高频、大体积事件，
减少 agent_run_events 表占用。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import Any, Callable

from daily_agent.stream_protocol import StreamEvent
from daily_agent.errors import MemoryError as DailyAgentMemoryError

logger = logging.getLogger(__name__)

# 只推前端、不落库的事件类型。
_NON_PERSISTENT_STREAM_TYPES = frozenset(
    {
        "response_delta",
        "status_updated",
    }
)

AppendEvents = Callable[[list[StreamEvent]], None]


def should_persist_stream_event(event: StreamEvent) -> bool:
    """判断事件是否值得写入 agent_run_events。"""
    if event.type in _NON_PERSISTENT_STREAM_TYPES:
        return False
    if event.type != "trace_updated":
        return True
    trace = event.payload.get("trace")
    if not isinstance(trace, dict):
        return True
    stage = str(trace.get("stage") or "")
    status = str(trace.get("status") or "")
    # reasoning 流式增量只用于实时 UI，完成态单独落库即可。
    if stage == "reasoning" and status == "running":
        return False
    return True


def compact_stream_event_for_storage(event: StreamEvent) -> StreamEvent:
    """压缩落库 payload，避免把完整 Prompt 或长 reasoning 全文写入 MySQL。"""
    if event.type != "trace_updated":
        return event
    payload = dict(event.payload)
    trace = payload.get("trace")
    if not isinstance(trace, dict):
        return event
    trace = dict(trace)
    details = dict(trace.get("details") or {})
    stage = str(trace.get("stage") or "")

    if stage == "prompt_ready" and isinstance(details.get("prompt_snapshot"), dict):
        snapshot = details["prompt_snapshot"]
        instructions = snapshot.get("instructions")
        details["prompt_snapshot"] = {
            "run_id": snapshot.get("run_id"),
            "model_id": snapshot.get("model_id"),
            "context_token_budget": snapshot.get("context_token_budget"),
            "instruction_count": len(instructions) if isinstance(instructions, list) else 0,
            "tool_count": len(snapshot.get("tool_names") or []),
            "skill_count": len(snapshot.get("skill_names") or []),
            "storage_compact": True,
        }

    reasoning_content = details.get("reasoning_content")
    if isinstance(reasoning_content, str) and len(reasoning_content) > 800:
        details["reasoning_content"] = reasoning_content[:800] + "…"
        details["storage_truncated"] = True

    trace["details"] = details
    payload["trace"] = trace
    return replace(event, payload=payload)


class BatchedRunEventWriter:
    """按运行隔离缓冲；后台批写与终态刷新只竞争同一个 run。

    终态刷新可以自行写出尚未被后台领取的事件，因此不会排在其他用户的慢写入后。
    同一 run 最多一个写入者，刷新等待该 run 的在途写入完成，保证终态前事件完整。
    """

    def __init__(self, append_events: AppendEvents, *, batch_size: int = 24,
                 flush_interval_seconds: float = 0.2) -> None:
        self._append_events = append_events
        self._batch_size = max(1, int(batch_size))
        self._flush_interval_seconds = max(0.05, float(flush_interval_seconds))
        self._condition = threading.Condition()
        self._pending: dict[str, list[StreamEvent]] = {}
        self._due: dict[str, float] = {}
        self._inflight: set[str] = set()
        self._errors: dict[str, Exception] = {}
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="sb-agent-run-event-writer", daemon=True)
        self._thread.start()

    def schedule(self, event: StreamEvent) -> None:
        if not should_persist_stream_event(event):
            return
        compacted = compact_stream_event_for_storage(event)
        with self._condition:
            if self._stopping:
                raise DailyAgentMemoryError("运行事件写入器已经关闭")
            self._raise_error(event.run_id)
            self._pending.setdefault(event.run_id, []).append(compacted)
            # 从本批首条事件计时，持续有小流量时也不会无限推迟刷新。
            self._due.setdefault(event.run_id, time.monotonic() + self._flush_interval_seconds)
            self._condition.notify_all()

    def flush(self, timeout_seconds: float = 10.0, *, run_id: str | None = None) -> None:
        """只等待指定运行；不指定时用于兼容调用和退出前刷新所有运行。"""
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self._condition:
            run_ids = [run_id] if run_id is not None else list(
                dict.fromkeys([*self._pending, *self._inflight, *self._errors])
            )
        for current_run_id in run_ids:
            while True:
                with self._condition:
                    self._raise_error(current_run_id)
                    if current_run_id not in self._inflight and not self._pending.get(current_run_id):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DailyAgentMemoryError("等待当前运行事件写入超时")
                    if current_run_id in self._inflight:
                        self._condition.wait(timeout=remaining)
                        continue
                    batch = self._take(current_run_id)
                self._write_batch(current_run_id, batch)

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        try:
            self.flush(timeout_seconds=timeout_seconds)
        finally:
            self._thread.join(timeout=max(0.1, float(timeout_seconds)))

    def forget_run(self, run_id: str) -> None:
        """终态成功保存后清理故障标记，避免失败运行在常驻进程中长期累积。"""
        with self._condition:
            if run_id not in self._inflight:
                self._errors.pop(run_id, None)

    def _take(self, run_id: str) -> list[StreamEvent]:
        """调用者持有条件锁；一次只领取同一运行的有限批次。"""
        items = self._pending[run_id]
        batch = items[:self._batch_size]
        del items[:self._batch_size]
        if not items:
            self._pending.pop(run_id, None)
            self._due.pop(run_id, None)
        else:
            # 将仍有积压的运行移到队尾，避免持续产出事件的用户独占后台写入者。
            self._pending.pop(run_id)
            self._pending[run_id] = items
        self._inflight.add(run_id)
        return batch

    def _run(self) -> None:
        while True:
            with self._condition:
                selected = None
                now = time.monotonic()
                for run_id, items in self._pending.items():
                    if run_id in self._inflight or run_id in self._errors:
                        continue
                    if self._stopping or len(items) >= self._batch_size or self._due[run_id] <= now:
                        selected = run_id
                        break
                if selected is None:
                    if self._stopping:
                        return
                    self._condition.wait(timeout=self._flush_interval_seconds)
                    continue
                batch = self._take(selected)
            self._write_batch(selected, batch)

    def _write_batch(self, run_id: str, batch: list[StreamEvent]) -> None:
        try:
            self._append_events(batch)
        except Exception as exc:
            # 错误必须传给终态刷新，不能吞掉异常后向前端宣称保存成功。
            with self._condition:
                self._errors[run_id] = exc
                self._pending.pop(run_id, None)
                self._due.pop(run_id, None)
            logger.exception("运行事件写入失败：run_id=%s count=%s", run_id, len(batch))
        finally:
            with self._condition:
                self._inflight.discard(run_id)
                self._condition.notify_all()

    def _raise_error(self, run_id: str) -> None:
        if run_id in self._errors:
            raise DailyAgentMemoryError("当前运行事件保存失败") from self._errors[run_id]
