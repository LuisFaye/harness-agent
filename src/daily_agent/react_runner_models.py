"""AgentLoopRunner 暂停态记录的数据结构，独立成模块避免循环导入。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PendingReactRun:
    run_id: str
    status: str
    run_output: Any
    updated_at: str | None = None
    expires_at: str | None = None
