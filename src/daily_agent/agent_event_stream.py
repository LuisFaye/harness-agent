"""把 Agno 异步事件流桥接成现有同步 Runner 可消费的迭代器。

AgentLoopRunner 的上层 API 仍是同步的，但 Agno 只有 ``arun`` / ``acontinue_run``
会并行执行模型同一批返回的多个 Tool Call。本模块在独立线程中维护 asyncio 事件
循环，通过有界队列逐条回传事件，既保留 NDJSON 实时流，也不要求改写整个服务栈。
不提供异步方法的测试替身或兼容 Agent 会自动回退到原同步方法。
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import queue
import threading
from collections.abc import Callable, Iterator

from agno.exceptions import RunCancelledException
from typing import Any

_ITEM = "item"
_ERROR = "error"
_DONE = "done"
_QUEUE_SIZE = 64


def stream_agent_run(agent: Any, input_value: Any, *, cancellation_check: Callable[[], bool] | None = None, **kwargs: Any) -> Any:
    """优先消费 ``agent.arun``，没有异步入口时保持原同步行为。"""
    async_method = getattr(agent, "arun", None)
    if callable(async_method):
        return _bridge_async_stream(async_method, input_value, cancellation_check=cancellation_check, **kwargs)
    return agent.run(input_value, **kwargs)


def stream_agent_continuation(agent: Any, *, cancellation_check: Callable[[], bool] | None = None, **kwargs: Any) -> Any:
    """确认后继续运行时同样优先使用 ``acontinue_run``。"""
    async_method = getattr(agent, "acontinue_run", None)
    if callable(async_method):
        return _bridge_async_stream(async_method, cancellation_check=cancellation_check, **kwargs)
    return agent.continue_run(**kwargs)


def _bridge_async_stream(method: Any, *args: Any, cancellation_check: Callable[[], bool] | None = None, **kwargs: Any) -> Iterator[Any]:
    events: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=_QUEUE_SIZE)
    stopped = threading.Event()
    # 保存事件循环与消费任务，让同步端停止时可以中断正在等待网络的异步任务。
    running: dict[str, Any] = {}
    # 主线程里开启的 ContextVar（例如模型请求截获）必须复制到异步桥线程，
    # 否则模型实际调用发生在新线程时读不到收集器，运行详情会一直是空数组。
    parent_context = contextvars.copy_context()

    def put(kind: str, payload: Any = None) -> None:
        while not stopped.is_set():
            try:
                events.put((kind, payload), timeout=0.1)
                return
            except queue.Full:
                continue

    async def consume() -> None:
        running["loop"] = asyncio.get_running_loop()
        running["task"] = asyncio.current_task()
        try:
            result = method(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            if hasattr(result, "__aiter__"):
                async for item in result:
                    put(_ITEM, item)
            elif result is not None:
                put(_ITEM, result)
        except BaseException as exc:
            put(_ERROR, exc)
        finally:
            put(_DONE)

    def worker() -> None:
        parent_context.run(asyncio.run, consume())

    thread = threading.Thread(target=worker, daemon=True, name="sb-agent-async-tools")
    thread.start()
    try:
        while True:
            # 模型还未输出时队列可能一直为空，必须主动检查停止信号。
            if cancellation_check and cancellation_check():
                raise RunCancelledException("用户取消任务")
            try:
                kind, payload = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if kind == _ITEM:
                yield payload
                continue
            if kind == _ERROR:
                raise payload
            if kind == _DONE:
                return
    finally:
        stopped.set()
        loop = running.get("loop")
        task = running.get("task")
        if loop is not None and task is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                # 消费任务可能恰好结束并关闭事件循环，无需再重复取消。
                pass
        thread.join(timeout=1)
