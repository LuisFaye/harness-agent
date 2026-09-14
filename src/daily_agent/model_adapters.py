"""OpenAI 兼容模型的项目内薄适配。

背景：Agno 官方 OpenAIChat._get_metrics() 只解析 OpenAI 标准字段
（prompt_tokens_details.cached_tokens），而 DeepSeek 等兼容服务把
prompt_cache_hit_tokens / prompt_cache_miss_tokens 放在 CompletionUsage
的额外字段里，官方实现会直接忽略它们，导致缓存命中/未命中 Token
无法进入运行 metadata 和前端“上下文用量”弹窗。

本模块通过子类复写 _get_metrics() 在不修改 site-packages 的前提下补齐：
1. DeepSeek 明确上报的 hit 写入标准 metrics.cache_read_tokens；
2. hit/miss 原样写入 metrics.provider_metrics 稳定键，0 值也会保留，
   经过 Agno 聚合（ModelMetrics.accumulate 会合并 provider_metrics）
   后仍能判断“供应商明确上报”，而不是把 dataclass 默认 0 当上报；
3. 不把 miss 冒充 cache_write_tokens，避免污染“缓存写入”语义。
"""

from __future__ import annotations

import logging
from math import isfinite
from typing import Any

from agno.models.metrics import MessageMetrics
from agno.models.openai import OpenAIChat
from daily_agent.model_request_capture import (
    is_capturing_model_requests,
    record_openai_compatible_request,
)
from daily_agent.model_stream import resilient_model_stream
from daily_agent.tool_call_validation import validate_tool_calls

logger = logging.getLogger(__name__)

try:
    from openai.types import CompletionUsage
except (ImportError, ModuleNotFoundError):  # pragma: no cover - 依赖缺失时保持可导入
    CompletionUsage = Any  # type: ignore[misc, assignment]


# provider_metrics 中使用的稳定键，运行结束采集时会从这里汇总明确上报的
# 缓存命中/未命中 Token，包括明确上报的 0。
PROVIDER_KEY_CACHE_HIT = "prompt_cache_hit_tokens"
PROVIDER_KEY_CACHE_MISS = "prompt_cache_miss_tokens"


def _read_extra_non_negative_int(value: Any, key: str) -> int | None:
    """读取 CompletionUsage 额外字段，只接受非负有限数值。

    DeepSeek 的 prompt_cache_hit_tokens / prompt_cache_miss_tokens 属于
    Pydantic 的额外字段，优先从 model_extra 读取，再回退到属性访问，
    兼容不同版本 openai SDK 的返回结构。布尔值、负数、NaN 等异常值
    一律视为“未上报”，避免把脏数据带进运行用量。
    """
    raw: Any = None
    model_extra = getattr(value, "model_extra", None)
    if isinstance(model_extra, dict) and key in model_extra:
        raw = model_extra[key]
    else:
        raw = getattr(value, key, None)
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if not isfinite(raw) or raw < 0:
        return None
    return int(raw)


class OpenAICompatibleChat(OpenAIChat):
    """OpenAI 兼容模型薄适配，主要补齐 DeepSeek 的缓存用量字段。"""

    def invoke(self, *args: Any, **kwargs: Any):
        """同步调用前截获 OpenAI 兼容请求体。"""
        self._capture_outgoing_chat_request(*args, stream=False, **kwargs)
        return super().invoke(*args, **kwargs)

    async def ainvoke(self, *args: Any, **kwargs: Any):
        """异步非流式调用前截获 OpenAI 兼容请求体。"""
        self._capture_outgoing_chat_request(*args, stream=False, **kwargs)
        return await super().ainvoke(*args, **kwargs)

    def invoke_stream(self, *args: Any, **kwargs: Any):
        """同步流式调用前截获 OpenAI 兼容请求体。"""
        self._capture_outgoing_chat_request(*args, stream=True, **kwargs)
        return super().invoke_stream(*args, **kwargs)

    async def ainvoke_stream(self, *args: Any, **kwargs: Any):
        """异步流：先截获本轮请求，再复用官方解析并加上空闲重连。

        注意：必须在 resilient_model_stream 外层截获一次。流重连只会再次进入
        ``super().ainvoke_stream``，不会重复记入同一逻辑轮次。
        """
        self._capture_outgoing_chat_request(*args, stream=True, **kwargs)
        invoke = super().ainvoke_stream
        async for response in resilient_model_stream(lambda: invoke(*args, **kwargs)):
            yield response

    def _capture_outgoing_chat_request(self, *args: Any, stream: bool = False, **kwargs: Any) -> None:
        """组装与 chat.completions.create 一致的 OpenAI 兼容 JSON 并记入收集器。"""
        if not is_capturing_model_requests():
            return
        try:
            messages = kwargs.get("messages", args[0] if args else None)
            if messages is None:
                return
            compress_tool_results = bool(kwargs.get("compress_tool_results", False))
            if len(args) >= 7:
                compress_tool_results = bool(args[6])
            formatted_messages = self._format_all_messages(messages, compress_tool_results)
            request_params = self.get_request_params(
                response_format=kwargs.get("response_format", args[2] if len(args) > 2 else None),
                tools=kwargs.get("tools", args[3] if len(args) > 3 else None),
                tool_choice=kwargs.get("tool_choice", args[4] if len(args) > 4 else None),
                run_response=kwargs.get("run_response", args[5] if len(args) > 5 else None),
            )
            body: dict[str, Any] = {
                "model": self.id,
                "messages": formatted_messages,
                **request_params,
            }
            if stream:
                body["stream"] = True
                body.setdefault("stream_options", {"include_usage": True})
            record_openai_compatible_request(body)
        except Exception:
            # 截获失败绝不能影响真实推理；只留日志便于排查监控缺口。
            logger.exception("组装模型请求快照失败")

    def _get_metrics(self, response_usage: CompletionUsage) -> MessageMetrics:
        # 先复用官方实现，得到 input/output/total、标准 cached_tokens、
        # reasoning_tokens 等字段；官方实现不会解析 DeepSeek 额外字段。
        metrics = super()._get_metrics(response_usage)

        # DeepSeek 等兼容服务会明确返回这两个字段（可能为 0）。
        hit_tokens = _read_extra_non_negative_int(
            response_usage, PROVIDER_KEY_CACHE_HIT
        )
        miss_tokens = _read_extra_non_negative_int(
            response_usage, PROVIDER_KEY_CACHE_MISS
        )

        provider_metrics: dict[str, int] = {}
        if hit_tokens is not None:
            # DeepSeek 语义：prompt_tokens = hit + miss，hit 直接作为标准
            # cache_read_tokens；0 也要保留，表示供应商明确上报了 0 命中。
            metrics.cache_read_tokens = hit_tokens
            provider_metrics[PROVIDER_KEY_CACHE_HIT] = hit_tokens
        if miss_tokens is not None:
            # miss 只进 provider_metrics，不冒充 cache_write_tokens。
            provider_metrics[PROVIDER_KEY_CACHE_MISS] = miss_tokens

        if not provider_metrics:
            # 没有 DeepSeek 额外字段时，回退到标准 OpenAI 口径：
            # prompt_tokens_details.cached_tokens 明确存在（含 0）时，
            # 用同一 provider_metrics 键保留 hit，并用 input - hit 推导 miss；
            # input 缺失或推导为负时不写 miss，绝不伪造供应商未提供的数据。
            prompt_token_details = getattr(response_usage, "prompt_tokens_details", None)
            cached_tokens = getattr(prompt_token_details, "cached_tokens", None)
            if cached_tokens is not None and not isinstance(cached_tokens, bool):
                if isinstance(cached_tokens, (int, float)) and isfinite(cached_tokens) and cached_tokens >= 0:
                    cached_value = int(cached_tokens)
                    provider_metrics[PROVIDER_KEY_CACHE_HIT] = cached_value
                    prompt_tokens = getattr(response_usage, "prompt_tokens", None)
                    if (
                        isinstance(prompt_tokens, (int, float))
                        and not isinstance(prompt_tokens, bool)
                        and isfinite(prompt_tokens)
                        and prompt_tokens >= cached_value
                    ):
                        provider_metrics[PROVIDER_KEY_CACHE_MISS] = int(prompt_tokens) - cached_value

        if provider_metrics:
            # MessageMetrics.provider_metrics 是 Agno 官方定义的透传字段，
            # accumulate_model_metrics 会把它合并进 RunOutput.metrics.details
            # 对应模型条目的 provider_metrics，从而保留“明确上报”和 0 值。
            metrics.provider_metrics = provider_metrics
        return metrics

    def get_function_calls_to_run(self, assistant_message, messages, functions=None):
        """参数有效后才交给 SDK 的执行/确认分支，避免空命令先要求用户授权。"""
        return validate_tool_calls(super().get_function_calls_to_run(
            assistant_message=assistant_message, messages=messages, functions=functions,
        ))

    def get_function_call_to_run_from_tool_execution(self, tool_execution, functions=None):
        """已保存的暂停记录也必须重新校验，防止升级前的空命令在确认后被执行。"""
        call = super().get_function_call_to_run_from_tool_execution(tool_execution, functions)
        return validate_tool_calls([call])[0]
