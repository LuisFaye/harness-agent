from __future__ import annotations

from daily_agent.executor import ExecutionResult, StepObservation
from daily_agent.planner import TaskPlan
from daily_agent.services.agent_models import TraceEvent


def build_cancelled_execution(
    *,
    plan: TaskPlan,
    run_id: str,
    execution_source: str,
    observations: list[StepObservation] | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        status="cancelled",
        response_text="任务已取消。",
        plan=plan,
        observations=list(observations or []),
        stop_reason="用户取消任务",
        missing_info=[],
        run_id=run_id,
        execution_source=execution_source,
        error_stage="cancellation",
    )


def build_cancellation_trace_event() -> TraceEvent:
    return TraceEvent(
        key="cancelled",
        stage="cancelled",
        status="cancelled",
        title="任务已取消",
        summary="执行器已确认停止，不会继续开始新的工具步骤。",
        details={},
    )
