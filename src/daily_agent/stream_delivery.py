"""在线问答的容量控制和线程到异步 HTTP 流的传输桥。"""

from __future__ import annotations

import asyncio
import os
import threading
from collections import deque
from contextlib import contextmanager

from fastapi import HTTPException


class AskCapacity:
    """限制正在执行的问答；满载立即返回，避免等待者耗尽公共线程池。"""

    def __init__(self) -> None:
        self.limit = max(1, int(os.getenv("SB_AGENT_MAX_CONCURRENT_ASKS", "10")))
        self._slots = threading.BoundedSemaphore(self.limit)

    def acquire(self) -> None:
        if not self._slots.acquire(blocking=False):
            raise HTTPException(
                status_code=429,
                detail={"code": "agent_busy", "message": "当前请求较多，请稍后重试"},
                headers={"Retry-After": "2"},
            )

    def release(self) -> None:
        self._slots.release()

    @contextmanager
    def slot(self):
        self.acquire()
        try:
            yield
        finally:
            self.release()


class StreamDelivery:
    """异步等待增量，不为每条空闲连接占用一个同步工作线程。

    缓冲区最多积累约 1 MiB 文本，慢客户端通过条件变量反压生产线程。
    断开后释放缓冲并唤醒生产者；任务仍可继续保存历史，不再积累推送数据。
    """

    def __init__(self, max_bytes: int = 1024 * 1024) -> None:
        self._items: deque[tuple[str | None, int]] = deque()
        self._condition = threading.Condition()
        self._size = 0
        self._max_bytes = max_bytes
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready: asyncio.Event | None = None

    def put(self, item: str | None) -> None:
        size = len(item.encode("utf-8")) if item is not None else 0
        with self._condition:
            # 单条超大事件允许独占缓冲，避免永远无法入队。
            while self._items and self._size + size > self._max_bytes and not self._closed:
                self._condition.wait()
            if self._closed:
                return
            self._items.append((item, size))
            self._size += size
            if self._loop is not None and self._ready is not None:
                try:
                    self._loop.call_soon_threadsafe(self._ready.set)
                except RuntimeError:
                    self._close_locked()

    def _close_locked(self) -> None:
        self._closed = True
        self._items.clear()
        self._size = 0
        self._condition.notify_all()

    async def iterate(self):
        with self._condition:
            self._loop = asyncio.get_running_loop()
            self._ready = asyncio.Event()
        try:
            while True:
                with self._condition:
                    if self._items:
                        item, size = self._items.popleft()
                        self._size -= size
                        self._condition.notify_all()
                        available = True
                    else:
                        self._ready.clear()
                        available = False
                if not available:
                    await self._ready.wait()
                    continue
                if item is None:
                    return
                yield item
        finally:
            with self._condition:
                self._close_locked()
