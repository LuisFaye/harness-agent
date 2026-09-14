"""Agent 运行生命周期业务服务。"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from daily_agent.run_lifecycle_repository import MySQLRunLifecycleRepository
from daily_agent.errors import MemoryError as DailyAgentMemoryError
from daily_agent.model_request_store import save_model_requests
from daily_agent.run_event_persistence import BatchedRunEventWriter, compact_stream_event_for_storage, should_persist_stream_event
from daily_agent.run_models import RunCancelResponse, RunEventItem, RunEventListResponse
from daily_agent.run_repository import TaskHistoryRunRepository
from daily_agent.services.run_service import sanitize_run_value
from daily_agent.shell_tools import cancel_shell_processes_for_run
from daily_agent.stream_protocol import StreamEvent

CancelNotifier = Callable[[], None]
TerminalEventFactory = Callable[[str, dict[str, Any]], StreamEvent]
logger = logging.getLogger(__name__)


class _ActiveRun:
    def __init__(self) -> None:
        # 取消和完成只需按运行互斥，不能持有全局锁等待某个用户的数据库写入。
        self.lock = threading.RLock()
        self.cancel_requested = threading.Event()
        self.notifier: CancelNotifier | None = None


class RunLifecycleService:
    """保证运行主记录、终态和终态事件按统一规则写入。"""

    def __init__(
        self,
        repository: MySQLRunLifecycleRepository | None = None,
        legacy_repository: TaskHistoryRunRepository | None = None,
        *,
        async_event_persistence: bool = False,
    ) -> None:
        self.repository = repository or MySQLRunLifecycleRepository()
        self.legacy_repository = legacy_repository or TaskHistoryRunRepository()
        self._lock = threading.RLock()
        self._active_runs: dict[str, _ActiveRun] = {}
        self._event_writer: BatchedRunEventWriter | None = None
        if async_event_persistence:
            self._event_writer = BatchedRunEventWriter(self.repository.append_events)

    def start_run(
        self,
        *,
        run_id: str,
        user_id: str,
        session_id: str,
        query_text: str,
        persist_user_message: bool = False,
        attachment_ids: list[str] | None = None,
    ) -> None:
        self.repository.create_run(
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            query_text=query_text,
            status="running",
            started_at=datetime.now(timezone.utc),
            persist_user_message=persist_user_message,
            attachment_ids=attachment_ids,
        )
        with self._lock:
            self._active_runs[run_id] = _ActiveRun()

    def bind_cancel_notifier(self, *, run_id: str, notifier: CancelNotifier) -> None:
        """把取消受理事件送回当前 HTTP 流；非流式运行可以不绑定。"""
        with self._lock:
            active = self._active_runs.setdefault(run_id, _ActiveRun())
            active.notifier = notifier

    def is_cancel_requested(self, run_id: str, *, user_id: str = "default") -> bool:
        with self._lock:
            active = self._active_runs.get(run_id)
            if active is not None:
                return active.cancel_requested.is_set()
        record = self.repository.get_run(user_id=user_id, run_id=run_id)
        return bool(record and record.status in {"cancel_requested", "cancelled"})

    def request_cancel(self, *, run_id: str, user_id: str) -> RunCancelResponse | None:
        """幂等提交停止请求，同时通知 Agno 和当前流。"""
        notifier: CancelNotifier | None = None
        with self._lock:
            active = self._active_runs.get(run_id)
        with active.lock if active is not None else threading.RLock():
            status, changed = self.repository.request_cancel(run_id=run_id, user_id=user_id)
            if status is None:
                return None
            if status in {"cancel_requested", "cancelled"}:
                if active is not None:
                    active.cancel_requested.set()
                    if changed:
                        notifier = active.notifier
                accepted = True
                message = (
                    "任务已经取消。"
                    if status == "cancelled"
                    else "已提交停止请求，正在等待当前执行步骤结束。"
                )
                # 锁内只完成状态登记；实际通知与 Shell 进程停止放到锁外执行，
                # 避免在自身 RLock 内串行等待最多 4×3 秒（Shell 终止已后台并发）。
                should_notify = True
            else:
                should_notify = False
                accepted = False
                message = "任务已经结束，不能再停止。"
        # 除 Agno 取消外，同时停止该 run 的 Shell 子进程；
        # executor_notified 表示任一执行器已收到取消。
        if should_notify:
            agno_notified = _cancel_agno_run(run_id)
            shell_notified = cancel_shell_processes_for_run(run_id, user_id=user_id)
            executor_notified = agno_notified or shell_notified
        else:
            executor_notified = False
        if notifier is not None:
            try:
                notifier()
            except Exception:
                logger.exception("无法把取消受理事件写回运行流：run_id=%s", run_id)
        return RunCancelResponse(
            run_id=run_id,
            status=status,
            accepted=accepted,
            executor_notified=executor_notified,
            message=message,
        )

    def schedule_event(self, event: StreamEvent) -> None:
        """异步排队落库；HTTP 流应先推送再调用本方法。"""
        if self._event_writer is not None:
            self._event_writer.schedule(event)
            return
        self._persist_event_sync(event)

    def record_event(self, event: StreamEvent) -> None:
        """同步落库；测试与兼容路径使用。"""
        self._persist_event_sync(event)

    def flush_events(self, timeout_seconds: float = 10.0, *, run_id: str | None = None) -> None:
        """在写入终态前刷出已排队的事件，保证 sequence 顺序可读。"""
        if self._event_writer is not None:
            self._event_writer.flush(timeout_seconds=timeout_seconds, run_id=run_id)

    def shutdown_event_writer(self, timeout_seconds: float = 5.0) -> None:
        if self._event_writer is not None:
            self._event_writer.shutdown(timeout_seconds=timeout_seconds)

    def _persist_event_sync(self, event: StreamEvent) -> None:
        if not should_persist_stream_event(event):
            return
        self.repository.append_event(compact_stream_event_for_storage(event))

    def complete_run(
        self,
        *,
        run_id: str,
        user_id: str,
        result: dict[str, Any],
        terminal_event: StreamEvent | None = None,
        terminal_event_factory: TerminalEventFactory | None = None,
    ) -> tuple[dict[str, Any], StreamEvent]:
        self.flush_events(run_id=run_id)
        with self._lock:
            active = self._active_runs.get(run_id)
        with active.lock if active is not None else threading.RLock():
            final_result = dict(result)
            cancel_requested = bool(
                final_result.get("status") == "cancelled"
                or (active is not None and active.cancel_requested.is_set())
            )
            if cancel_requested:
                final_result["status"] = "cancelled"
                final_result["stop_reason"] = "用户取消任务"
                final_result["error_stage"] = "cancellation"
                if not str(final_result.get("response_text") or "").strip():
                    final_result["response_text"] = "任务已取消。"
            event_type = "run_cancelled" if cancel_requested else "run_completed"
            if terminal_event_factory is not None:
                event = terminal_event_factory(event_type, final_result)
            elif terminal_event is not None:
                event = terminal_event
            else:
                raise ValueError("完成运行时必须提供 terminal_event 或 terminal_event_factory")

            prompt_snapshot = (
                final_result.get("prompt_snapshot")
                if isinstance(final_result.get("prompt_snapshot"), dict)
                else {}
            )
            status = str(final_result.get("status") or "error")
            error_message = (
                str(final_result.get("stop_reason") or "")
                if status in {"error", "max_iterations"}
                else None
            )
            # 模型请求只落本地文件；落库 metadata 再剔一次，防止旧调用方仍塞进来。
            if isinstance(final_result.get("model_requests"), list):
                requests = [item for item in final_result["model_requests"] if isinstance(item, dict)]
                if requests:
                    save_model_requests(run_id, requests, user_id=user_id)
                final_result = {key: value for key, value in final_result.items() if key != "model_requests"}
            self.repository.finalize_run(
                run_id=run_id,
                user_id=user_id,
                status=status,
                response_text=str(final_result.get("response_text") or ""),
                execution_source=_optional_text(final_result.get("execution_source")),
                model_id=_optional_text(prompt_snapshot.get("model_id")),
                error_stage=_optional_text(final_result.get("error_stage")),
                error_message=error_message,
                metadata=final_result,
                terminal_event=event,
                finished_at=datetime.now(timezone.utc),
            )
            with self._lock:
                self._active_runs.pop(run_id, None)
            return final_result, event

    def fail_run(
        self,
        *,
        run_id: str,
        user_id: str,
        error_payload: dict[str, Any],
        terminal_event: StreamEvent,
    ) -> None:
        try:
            self.flush_events(run_id=run_id)
        except DailyAgentMemoryError:
            # 事件写入故障本身也需要落下失败终态，不能因重复刷新故障而永远停在 running。
            logger.exception("失败运行的事件未能全部保存：run_id=%s", run_id)
        self.repository.finalize_run(
            run_id=run_id,
            user_id=user_id,
            status="error",
            response_text="",
            execution_source=None,
            model_id=None,
            error_stage=_optional_text(error_payload.get("stage")),
            error_message=_optional_text(error_payload.get("message")),
            metadata={"error": error_payload},
            terminal_event=terminal_event,
            finished_at=datetime.now(timezone.utc),
        )
        if self._event_writer is not None:
            self._event_writer.forget_run(run_id)
        with self._lock:
            self._active_runs.pop(run_id, None)

    def list_events(
        self,
        *,
        user_id: str,
        run_id: str,
        after_sequence: int,
        limit: int,
    ) -> RunEventListResponse | None:
        run = self.repository.get_run(user_id=user_id, run_id=run_id)
        if run is None:
            legacy_run = self.legacy_repository.get_record(user_id=user_id, run_id=run_id)
            if legacy_run is None:
                return None
            # M1 旧记录没有逐条事件，返回明确空页，不把历史详情伪造成实时事件。
            return RunEventListResponse(
                run_id=run_id,
                events=[],
                last_sequence=after_sequence,
                has_more=False,
            )
        events, has_more = self.repository.list_events(
            run_id=run_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        items = [
            RunEventItem(
                version="2.0",
                run_id=event.run_id,
                sequence=event.sequence,
                type=event.event_type,
                timestamp=event.event_timestamp,
                payload=sanitize_run_value(event.payload, path="event.payload"),
            )
            for event in events
        ]
        last_sequence = items[-1].sequence if items else after_sequence
        return RunEventListResponse(
            run_id=run_id,
            events=items,
            last_sequence=last_sequence,
            has_more=has_more,
        )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _cancel_agno_run(run_id: str) -> bool:
    try:
        from agno.agent import Agent

        Agent.cancel_run(run_id)
        return True
    except Exception:
        logger.exception("Agno 取消信号发送失败：run_id=%s", run_id)
        return False
