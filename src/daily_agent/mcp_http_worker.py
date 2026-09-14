"""Streamable HTTP MCP 的单任务连接 Worker。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from daily_agent.errors import DailyAgentError


@dataclass
class HttpMcpFunction:
    """提供给现有 Tool 包装层使用的最小函数描述。"""

    description: str
    parameters: dict[str, Any]
    entrypoint: Callable[..., Any]
    requires_confirmation: bool = False


class HttpMcpToolkit:
    """保持远程 MCP 的整个生命周期都在同一个 asyncio Task 中。"""

    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        tool_name_prefix: str,
        timeout_seconds: int = 15,
    ) -> None:
        self.url = url
        self.headers = headers
        self.tool_name_prefix = tool_name_prefix.strip("_")
        self.timeout_seconds = timeout_seconds
        self.functions: dict[str, HttpMcpFunction] = {}
        self.session = _WorkerSession(self)
        self.capabilities: dict[str, list[dict[str, Any]]] = {
            "tools": [],
            "resources": [],
            "prompts": [],
        }
        self._queue: asyncio.Queue[tuple[str, Any, asyncio.Future[Any]]] | None = None
        self._ready: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._startup_error: BaseException | None = None
        self._closing = False
        self._max_concurrency = max(1, int(os.getenv("SB_AGENT_MCP_HTTP_CONCURRENCY", "4")))

    async def connect(self) -> None:
        self._queue = asyncio.Queue(maxsize=64)
        self._closing = False
        self._startup_error = None
        self._ready = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"mcp-http-{self.tool_name_prefix}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.timeout_seconds + 5)
        except BaseException:
            await self.close()
            raise
        if self._startup_error is not None:
            raise DailyAgentError(f"MCP 握手失败：{_plain_exception(self._startup_error)}")

    async def close(self) -> None:
        if self._task is None:
            return
        self._closing = True
        if not self._task.done():
            # 取消连接所有者，由其 finally 先收回请求子任务，再退出 MCP 上下文。
            self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._request("call_tool", {"name": name, "arguments": arguments})

    async def read_resource(self, uri: Any) -> Any:
        return await self._request("read_resource", uri)

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> Any:
        return await self._request("get_prompt", {"name": name, "arguments": arguments})

    async def _request(self, operation: str, payload: Any) -> Any:
        if self._closing or self._queue is None or self._task is None or self._task.done():
            raise DailyAgentError("MCP HTTP 连接已经关闭。")
        future = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait((operation, payload, future))
        except asyncio.QueueFull as exc:
            raise DailyAgentError("MCP HTTP 请求较多，请稍后重试。") from exc
        return await future

    async def _run(self) -> None:
        try:
            timeout = httpx.Timeout(self.timeout_seconds, read=300)
            async with httpx.AsyncClient(headers=self.headers, timeout=timeout) as client:
                async with streamable_http_client(self.url, http_client=client) as streams:
                    read, write = streams[0:2]
                    async with ClientSession(
                        read,
                        write,
                        read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                    ) as session:
                        initialization = await session.initialize()
                        await self._load_capabilities(session, initialization)
                        if self._ready is not None:
                            self._ready.set()
                        await self._serve(session)
        except BaseException as exc:
            self._startup_error = exc
            if self._ready is not None:
                self._ready.set()
            self._fail_pending(exc)

    async def _load_capabilities(self, session: ClientSession, initialization: Any) -> None:
        tool_page = await session.list_tools()
        tools = []
        for tool in tool_page.tools:
            raw_name = str(tool.name)
            public_name = self._public_tool_name(raw_name)
            input_schema = tool.inputSchema if isinstance(tool.inputSchema, dict) else {}
            function = HttpMcpFunction(
                description=str(tool.description or ""),
                parameters=input_schema or {"type": "object", "properties": {}},
                entrypoint=self._tool_entrypoint(raw_name),
            )
            self.functions[public_name] = function
            tools.append(
                {
                    "name": public_name,
                    "description": function.description,
                    "input_schema": function.parameters,
                    "requires_confirmation": False,
                }
            )

        server_capabilities = getattr(initialization, "capabilities", None)
        resources = []
        if getattr(server_capabilities, "resources", None) is not None:
            try:
                page = await session.list_resources()
                resources = [
                    {"uri": str(item.uri), "name": str(item.name or item.uri), "description": item.description}
                    for item in page.resources
                ]
            except Exception:
                resources = []

        prompts = []
        if getattr(server_capabilities, "prompts", None) is not None:
            try:
                page = await session.list_prompts()
                prompts = [
                    {
                        "name": str(item.name),
                        "description": item.description,
                        "arguments": [argument.model_dump(mode="json") for argument in item.arguments or []],
                    }
                    for item in page.prompts
                ]
            except Exception:
                prompts = []
        self.capabilities = {"tools": tools, "resources": resources, "prompts": prompts}

    async def _serve(self, session: ClientSession) -> None:
        if self._queue is None:
            return
        active: set[asyncio.Task] = set()
        try:
            while True:
                # 请求有独立 JSON-RPC ID，可共用会话并发等待；连接上下文仍由 _run 持有。
                if len(active) >= self._max_concurrency:
                    await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                    active = {task for task in active if not task.done()}
                operation, payload, future = await self._queue.get()
                if future.cancelled():
                    continue
                active = {task for task in active if not task.done()}
                task = asyncio.create_task(self._dispatch(session, operation, payload, future))
                # 上游超时或用户取消后，也要停止已派发的本地等待。
                future.add_done_callback(lambda done, task=task: task.cancel() if done.cancelled() else None)
                task.add_done_callback(lambda done, future=future: self._settle_cancelled(done, future))
                active.add(task)
        finally:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)

    @staticmethod
    def _settle_cancelled(task: asyncio.Task, future: asyncio.Future) -> None:
        # 子任务可能在第一次运行前即被关闭操作取消，此时其协程内的 finally 尚未进入。
        if task.cancelled() and not future.done():
            future.set_exception(DailyAgentError("MCP HTTP 请求已中止。"))

    async def _dispatch(self, session: ClientSession, operation: str, payload: Any,
                        future: asyncio.Future[Any]) -> None:
        try:
            result = await self._execute(session, operation, payload)
        except asyncio.CancelledError:
            if not future.done():
                future.set_exception(DailyAgentError("MCP HTTP 请求已中止。"))
            raise
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
        else:
            if not future.done():
                future.set_result(result)

    async def _execute(self, session: ClientSession, operation: str, payload: Any) -> Any:
        if operation == "call_tool":
            result = await session.call_tool(payload["name"], arguments=payload["arguments"])
            return _normalize_tool_result(result)
        if operation == "read_resource":
            return await session.read_resource(payload)
        if operation == "get_prompt":
            return await session.get_prompt(payload["name"], arguments=payload["arguments"] or None)
        raise DailyAgentError(f"未知 MCP HTTP 操作：{operation}")

    def _tool_entrypoint(self, raw_name: str):
        async def call(**kwargs: Any) -> Any:
            return await self.call_tool(raw_name, kwargs)

        call.__name__ = self._public_tool_name(raw_name)
        return call

    def _public_tool_name(self, raw_name: str) -> str:
        prefix = self.tool_name_prefix or "mcp"
        candidate = f"{prefix}_{raw_name}".replace("-", "_")
        return candidate[:64].rstrip("_")

    def _fail_pending(self, exc: BaseException) -> None:
        if self._queue is None:
            return
        while not self._queue.empty():
            _, _, future = self._queue.get_nowait()
            if not future.done():
                future.set_exception(exc)


class _WorkerSession:
    """兼容现有 Runtime 对 toolkit.session 的调用方式。"""

    def __init__(self, toolkit: HttpMcpToolkit) -> None:
        self.toolkit = toolkit

    async def read_resource(self, uri: Any) -> Any:
        return await self.toolkit.read_resource(uri)

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> Any:
        return await self.toolkit.get_prompt(name, arguments)


def _normalize_tool_result(result: Any) -> Any:
    content = list(getattr(result, "content", None) or [])
    text_items = [str(item.text) for item in content if getattr(item, "type", None) == "text"]
    if getattr(result, "isError", False):
        raise DailyAgentError("\n".join(text_items) or "MCP Tool 返回错误。")

    structured = getattr(result, "structuredContent", None)
    if len(text_items) == len(content):
        return "\n".join(text_items)
    return {
        "content": [item.model_dump(mode="json") for item in content],
        "structured_content": structured,
    }


def _plain_exception(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:500] or exc.__class__.__name__
