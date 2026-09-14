from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ErrorPayload:
    code: str
    message: str
    stage: str
    run_id: str
    details: dict[str, Any] = field(default_factory=dict)
    retriable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
            "run_id": self.run_id,
            "details": self.details,
            "retriable": self.retriable,
        }


class DailyAgentError(Exception):
    """面向用户的 sb-agent 异常基类。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "daily_agent_error",
        stage: str = "unknown",
        run_id: str = "-",
        details: dict[str, Any] | None = None,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.payload = ErrorPayload(
            code=code,
            message=message,
            stage=stage,
            run_id=run_id,
            details=details or {},
            retriable=retriable,
        )


class MissingConfigError(DailyAgentError):
    """缺少必需配置时抛出。"""


class DependencyError(DailyAgentError):
    """可选运行时依赖未安装时抛出。"""


class ExternalServiceError(DailyAgentError):
    """上游 API 调用失败或返回不可用数据时抛出。"""


class MemoryError(DailyAgentError):
    """记忆库无法读写时抛出。"""
