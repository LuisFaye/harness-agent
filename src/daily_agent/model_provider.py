from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from daily_agent.config import Settings
from daily_agent.errors import DependencyError, MissingConfigError

SUPPORTED_MODEL_PROVIDERS = {"openai_compatible", "dashscope"}
VISION_MODEL_MARKERS = ("vision",)


def model_supports_vision(model_id: str) -> bool:
    """判断当前模型 ID 是否支持图片输入（如 DeepSeek vision 系列）。"""
    normalized = str(model_id or "").strip().lower()
    return any(marker in normalized for marker in VISION_MODEL_MARKERS)


@dataclass(frozen=True)
class ModelConnectionResult:
    status: str
    provider: str
    model_id: str
    detail: str
    duration_ms: int
    checked_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "provider": self.provider,
            "model_id": self.model_id,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "checked_at": self.checked_at,
        }


Probe = Callable[[Settings, float], None]


class ModelConnectionTester:
    """真实检查模型服务，并用短缓存避免页面刷新时重复请求外部接口。"""

    def __init__(self, *, timeout_seconds: float = 8.0, cache_seconds: float = 30.0, probe: Probe | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self.probe = probe or _probe_openai_compatible_endpoint
        self._lock = threading.Lock()
        self._cache_key: str | None = None
        self._cached_result: ModelConnectionResult | None = None

    def check(self, settings: Settings, *, force: bool = False) -> ModelConnectionResult:
        cache_key = _settings_fingerprint(settings)
        now = time.time()
        with self._lock:
            if (
                not force
                and self._cache_key == cache_key
                and self._cached_result is not None
                and now - self._cached_result.checked_at < self.cache_seconds
            ):
                return self._cached_result

        started = time.perf_counter()
        if not settings.model_api_key:
            result = _connection_result(
                settings,
                status="error",
                detail="未配置模型 API Key，聊天功能不可用。",
                started=started,
            )
        elif settings.model_provider not in SUPPORTED_MODEL_PROVIDERS:
            result = _connection_result(
                settings,
                status="error",
                detail=f"不支持模型 Provider：{settings.model_provider}。",
                started=started,
            )
        else:
            try:
                self.probe(settings, self.timeout_seconds)
                result = _connection_result(
                    settings,
                    status="ok",
                    detail="模型服务连接成功，API Key 可用。",
                    started=started,
                )
            except Exception as exc:
                result = _connection_result(
                    settings,
                    status="error",
                    detail=_model_error_message(exc),
                    started=started,
                )

        with self._lock:
            self._cache_key = cache_key
            self._cached_result = result
        return result

    def clear(self) -> None:
        with self._lock:
            self._cache_key = None
            self._cached_result = None


def build_model(settings: Settings):
    """按 Provider 构建 Agno 模型，业务层不再直接依赖某一家模型厂商。"""
    if not settings.model_api_key:
        raise MissingConfigError("缺少模型 API Key，无法使用自然语言 ask 命令。")
    if settings.model_provider == "dashscope":
        try:
            from agno.models.dashscope import DashScope
        except ImportError as exc:
            raise DependencyError("缺少 agno 依赖，请先运行 pip install -e \".[dev]\"。") from exc
        return DashScope(
            id=settings.model_id,
            api_key=settings.model_api_key,
            base_url=settings.model_base_url,
        )
    if settings.model_provider == "openai_compatible":
        try:
            from daily_agent.model_adapters import OpenAICompatibleChat
        except ImportError as exc:
            raise DependencyError("缺少 agno 依赖，请先运行 pip install -e \".[dev]\"。") from exc
        # 使用项目内薄适配：除了补齐 DeepSeek 的缓存命中/未命中 Token
        # 采集外，初始化参数和 role_map 与官方 OpenAIChat 完全一致。
        return OpenAICompatibleChat(
            id=settings.model_id,
            api_key=settings.model_api_key,
            base_url=settings.model_base_url,
            # 限制单次网络空闲时间，不限制整个 Agent 任务的总时长。
            # 流式恢复由适配层统一负责，避免 SDK 与外层重试叠加长时间等待。
            timeout=httpx.Timeout(60.0, connect=10.0),
            max_retries=0,
            reasoning_effort=settings.model_reasoning_effort if settings.model_thinking_enabled else None,
            extra_body={
                "thinking": {
                    "type": "enabled" if settings.model_thinking_enabled else "disabled",
                }
            },
            # Agno 针对新版 OpenAI 默认把 system 改成 developer，但 DeepSeek 等兼容服务只接受 system。
            role_map={
                "system": "system",
                "user": "user",
                "assistant": "assistant",
                "tool": "tool",
                "model": "assistant",
            },
        )
    raise MissingConfigError(f"不支持模型 Provider：{settings.model_provider}。")


def _probe_openai_compatible_endpoint(settings: Settings, timeout_seconds: float) -> None:
    """调用 OpenAI 兼容的模型列表接口，验证网络、地址和鉴权是否同时可用。"""
    _fetch_openai_compatible_models(settings, timeout_seconds)


def fetch_available_models(settings: Settings, *, timeout_seconds: float = 8.0) -> list[str]:
    """读取当前 Provider 暴露的模型 ID 列表，供设置页下拉切换使用。"""
    if not settings.model_api_key:
        raise MissingConfigError("未配置模型 API Key，无法获取模型列表。")
    if settings.model_provider not in SUPPORTED_MODEL_PROVIDERS:
        raise MissingConfigError(f"不支持模型 Provider：{settings.model_provider}。")
    return _fetch_openai_compatible_models(settings, timeout_seconds)


def _fetch_openai_compatible_models(settings: Settings, timeout_seconds: float) -> list[str]:
    """调用 OpenAI 兼容 /models 接口并解析模型 ID。"""
    base_url = settings.model_base_url.rstrip("/")
    url = f"{base_url}/models"
    response = httpx.get(
        url,
        headers={"Authorization": f"Bearer {settings.model_api_key}"},
        timeout=timeout_seconds,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    model_ids: list[str] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    model_id = item.get("id")
                    if isinstance(model_id, str) and model_id.strip():
                        model_ids.append(model_id.strip())
                elif isinstance(item, str) and item.strip():
                    model_ids.append(item.strip())
        models = payload.get("models")
        if isinstance(models, list):
            for item in models:
                if isinstance(item, str) and item.strip():
                    model_ids.append(item.strip())
    deduped = list(dict.fromkeys(model_ids))
    if not deduped:
        raise ValueError("模型服务未返回可用模型列表。")
    return deduped


def _settings_fingerprint(settings: Settings) -> str:
    """缓存键只保存密钥摘要，避免把明文 Key 长期留在缓存索引中。"""
    raw = "|".join(
        [
            settings.model_provider,
            settings.model_id,
            settings.model_base_url,
            settings.model_api_key or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _connection_result(
    settings: Settings,
    *,
    status: str,
    detail: str,
    started: float,
) -> ModelConnectionResult:
    return ModelConnectionResult(
        status=status,
        provider=settings.model_provider,
        model_id=settings.model_id,
        detail=detail,
        duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
        checked_at=time.time(),
    )


def _model_error_message(exc: Exception) -> str:
    """只暴露便于修复的中文原因，不把请求头、Key 或长响应体带到页面。"""
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return f"模型鉴权失败（HTTP {status_code}），请检查 API Key。"
        if status_code == 404:
            return "模型地址不可用（HTTP 404），请检查 Base URL 是否包含正确的版本路径。"
        if status_code == 429:
            return "模型服务拒绝请求（HTTP 429），请检查额度或稍后重试。"
        return f"模型服务返回 HTTP {status_code}，请检查 Provider 和 Base URL。"
    if isinstance(exc, httpx.TimeoutException):
        return "模型连接超时，请检查网络或 Base URL。"
    if isinstance(exc, httpx.ConnectError):
        return "无法连接模型服务，请检查网络、代理和 Base URL。"
    return f"模型连接失败：{type(exc).__name__}。"


_DEFAULT_TESTER = ModelConnectionTester()


def get_model_connection_tester() -> ModelConnectionTester:
    return _DEFAULT_TESTER
