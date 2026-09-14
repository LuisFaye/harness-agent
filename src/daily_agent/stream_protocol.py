"""M2 流式事件协议。

这个模块只做三件简单的事：
1. 定义前后端允许传输的事件类型；
2. 给同一次运行的事件分配严格递增的 sequence；
3. 把事件序列化成一行一个 JSON 的 NDJSON。

把协议逻辑单独放在这里，是为了避免继续把状态和序列规则堆进 api.py。
后续增加事件落库时，也可以直接复用同一个事件对象。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

STREAM_PROTOCOL_VERSION = "2.0"

STREAM_EVENT_TYPES = frozenset(
    {
        "run_started",
        "status_updated",
        "trace_updated",
        "tool_started",
        "tool_finished",
        "response_started",
        "response_delta",
        "response_block",
        "response_completed",
        "run_waiting",
        "run_failed",
        "run_cancel_requested",
        "run_cancelled",
        "run_completed",
    }
)


@dataclass(frozen=True)
class StreamEvent:
    """一条可以直接写入 HTTP 流或运行事件表的标准事件。"""

    run_id: str
    sequence: int
    type: str
    timestamp: str
    payload: dict[str, Any]
    version: str = STREAM_PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "type": self.type,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    def to_ndjson(self) -> str:
        """返回一条以换行结束的 JSON，保证中文不被转成 Unicode 转义。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"


class StreamEventSequencer:
    """为单个 run 分配事件序号。

    当前 `/ask/stream` 会由 API 线程先写入 run_started，再由后台工作线程写入
    后续事件，所以这里使用锁保护计数器。这样即使以后 Tool 回调来自其他线程，
    也不会产生两个相同的 sequence。
    """

    def __init__(self, run_id: str) -> None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id 不能为空")
        self.run_id = normalized_run_id
        self._sequence = 0
        self._lock = threading.Lock()

    @property
    def last_sequence(self) -> int:
        with self._lock:
            return self._sequence

    def next(self, event_type: str, payload: dict[str, Any] | None = None) -> StreamEvent:
        if event_type not in STREAM_EVENT_TYPES:
            raise ValueError(f"未知的流式事件类型：{event_type}")
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("流式事件 payload 必须是对象")
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        return StreamEvent(
            run_id=self.run_id,
            sequence=sequence,
            type=event_type,
            timestamp=datetime.now().astimezone().isoformat(),
            payload=dict(payload or {}),
        )

    def serialize(self, event_type: str, payload: dict[str, Any] | None = None) -> str:
        return self.next(event_type, payload).to_ndjson()
