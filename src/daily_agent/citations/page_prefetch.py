"""在回答生成期间后台读取候选引用网页，避免最终回答被网络请求阻塞。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
import threading
import time

from daily_agent.citations.registry import WebCitationRegistry
from daily_agent.citations.web_fetch import fetch_page_text


logger = logging.getLogger(__name__)
MAX_PREFETCH_SOURCES = 5
MAX_PREFETCH_SECONDS = 10.0


class CitationPagePrefetcher:
    """把候选网页读取与模型回答生成重叠执行，只消费已经完成的结果。

    搜索 Tool 返回来源后立即提交网页读取。模型随后还要选择来源并生成回答，
    因此大部分网络等待可以被这段生成时间覆盖。最终组装引用时只读取已经完成
    的 Future，绝不等待仍在请求中的网页；未完成或失败的来源继续使用搜索摘要，
    保证来源增强不会拖慢回答结束。
    """

    def __init__(
        self,
        *,
        max_sources: int = MAX_PREFETCH_SOURCES,
        timeout_seconds: float = MAX_PREFETCH_SECONDS,
    ) -> None:
        self.max_sources = max(1, int(max_sources))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._executor: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future[str | None]] = {}
        self._closed = False
        self._lock = threading.Lock()

    def schedule(self, registry: WebCitationRegistry, source_ids: list[str]) -> None:
        """按搜索结果顺序预取候选来源，整个运行最多提交固定数量。"""
        sources, _, _ = registry.snapshot()
        source_by_id = {item.source_id: item for item in sources}
        with self._lock:
            if self._closed:
                return
            for source_id in source_ids:
                if source_id in self._futures or len(self._futures) >= self.max_sources:
                    continue
                source = source_by_id.get(source_id)
                if source is None:
                    continue
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=min(3, self.max_sources),
                        thread_name_prefix="citation-prefetch",
                    )
                deadline = time.monotonic() + self.timeout_seconds
                self._futures[source_id] = self._executor.submit(
                    fetch_page_text,
                    source.url,
                    deadline=deadline,
                )

    def completed_pages(self) -> dict[str, str]:
        """立即返回当前已完成的正文；此方法不会等待任何网络请求。"""
        with self._lock:
            completed = [
                (source_id, future)
                for source_id, future in self._futures.items()
                if future.done()
            ]
        pages: dict[str, str] = {}
        for source_id, future in completed:
            try:
                page_text = future.result()
            except Exception:
                logger.debug("引用网页后台预取失败：source_id=%s", source_id, exc_info=True)
                continue
            if page_text:
                pages[source_id] = page_text
        return pages

    def close(self) -> None:
        """停止接收新任务并取消尚未启动的请求，不等待运行中的网络线程。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
