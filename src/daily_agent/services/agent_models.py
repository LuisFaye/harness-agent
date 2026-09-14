from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from daily_agent.executor import ExecutionResult
from daily_agent.memory_extractor import ExtractedMemory
from daily_agent.planner import TaskPlan


@dataclass(frozen=True)
class TraceEvent:
    key: str
    stage: str
    status: str
    title: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "stage": self.stage,
            "status": self.status,
            "title": self.title,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass(frozen=True)
class AskServiceResult:
    query: str
    user_id: str
    session_id: str
    plan: TaskPlan
    execution: ExecutionResult
    extracted_memories: list[ExtractedMemory]
    trace: list[TraceEvent] = field(default_factory=list)
    prompt_snapshot: dict[str, Any] | None = None

    @property
    def response_text(self) -> str:
        return self.execution.response_text

    def to_metadata(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "plan": self.plan.to_metadata(),
            "execution": self.execution.to_metadata(),
            "extracted_memories": [item.__dict__ for item in self.extracted_memories],
            "trace": [item.to_metadata() for item in self.trace],
            "prompt_snapshot": self.prompt_snapshot,
        }
