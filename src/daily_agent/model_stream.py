"""限制模型流的空闲等待，并在尚未交付内容时恢复一次连接。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable

from agno.exceptions import ModelProviderError
from agno.models.response import ModelResponse

from daily_agent.react_text import is_transient_model_error

logger = logging.getLogger(__name__)
MODEL_STREAM_IDLE_SECONDS = 60.0


async def resilient_model_stream(
    create_stream: Callable[[], AsyncIterator[ModelResponse]],
) -> AsyncIterator[ModelResponse]:
    """只重发当前模型调用，绝不重放整个 Agent 或之前执行过的工具。

    收到正文、思考片段或工具参数后不再自动重试，否则可能重复输出，或把两次
    生成的工具参数拼在一起。空角色帧不能刷新空闲期限；正常持续输出不受总时长限制。
    """
    for attempt in range(2):
        stream = create_stream()
        delivered = False
        deadline = time.monotonic() + MODEL_STREAM_IDLE_SECONDS
        try:
            while True:
                remaining = max(0.001, deadline - time.monotonic())
                response = await asyncio.wait_for(anext(stream), timeout=remaining)
                # JSON 模式可能只返回空格并正常结束；这不算已经交付有效内容，
                # 也不能靠持续空格无限延长等待时间。
                content = response.content
                reasoning = response.reasoning_content
                if (isinstance(content, str) and content.strip()) or (
                    isinstance(reasoning, str) and reasoning.strip()
                ) or response.tool_calls or (bool(content) and not isinstance(content, str)):
                    delivered = True
                    deadline = time.monotonic() + MODEL_STREAM_IDLE_SECONDS
                yield response
        except StopAsyncIteration:
            if not delivered:
                if attempt == 0:
                    logger.warning("模型流正常结束但没有有效内容，重试当前模型调用一次")
                    continue
                raise ModelProviderError(
                    message="模型服务连续返回空回答，本轮未能生成内容，请稍后重试。",
                )
            return
        except Exception as exc:
            message = str(exc).lower()
            transient = isinstance(exc, TimeoutError) or is_transient_model_error(message) or any(
                token in message for token in (
                    "incomplete chunked read", "peer closed connection", "connection reset",
                    "connection error", "remoteprotocolerror",
                )
            )
            if not transient:
                raise
            if attempt == 0 and not delivered:
                logger.warning("模型流在有效输出前中断，重连当前模型调用一次：%s", type(exc).__name__)
                continue
            # 文案不包含外层历史重试规则的关键词，避免再次重跑工具循环。
            raise ModelProviderError(
                message="模型连接中断或长时间未返回有效内容，本轮已停止；已执行的工具不会自动重跑。请重试或继续。",
            ) from exc
        finally:
            await stream.aclose()
