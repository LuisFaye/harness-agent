"""仅在单次问答内复用提示词准备结果，避免诊断和执行重复读取上下文。"""

from __future__ import annotations

import inspect
import logging
import time
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)
_PREPARED: ContextVar[dict[tuple, Any] | None] = ContextVar("ask_prepared", default=None)


def preparation_scope(function: Callable) -> Callable:
    """每次问答独立缓存，结束即清理；不同用户、会话和后续轮次不共享。"""
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = _PREPARED.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _PREPARED.reset(token)
    return wrapped


def reuse_preparation(function: Callable) -> Callable:
    """按完整参数复用准备结果；未进入问答作用域时保持原有实时读取行为。"""
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        cache = _PREPARED.get()
        if cache is None:
            return function(*args, **kwargs)
        arguments = signature.bind(*args, **kwargs)
        arguments.apply_defaults()
        # 参数包含可变的 TaskPlan；取当次内容表示，避免按对象地址误复用。
        # self 不参与 key：同一问答作用域内不同实例的同参数调用（例如上下文构建）
        # 应该共享结果，否则会重复读一遍数据库。
        key_arguments = {
            name: value for name, value in arguments.arguments.items() if name != "self"
        }
        key = (function.__qualname__, repr(key_arguments))
        if key in cache:
            logger.info("request preparation reused: stage=%s", function.__name__)
            return cache[key]
        started = time.perf_counter()
        result = function(*args, **kwargs)
        cache[key] = result
        logger.info("request preparation built: stage=%s duration_ms=%.1f",
                    function.__name__, (time.perf_counter() - started) * 1000)
        return result
    return wrapped
