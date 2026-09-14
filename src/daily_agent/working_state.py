"""根据真实执行事件维护 Agent 当前任务的可观测工作状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any

from daily_agent.memory import MySQLMemoryStore


TERMINAL_PHASES = {"completed", "failed", "cancelled"}


@dataclass
class WorkingState:
    run_id: str
    goal_summary: str
    phase: str = "preparing"
    current_action: str = "正在准备任务"
    completed_steps: list[str] = field(default_factory=list)
    evidence_summaries: list[dict[str, Any]] = field(default_factory=list)
    active_constraints: list[str] = field(default_factory=list)
    blocker: str | None = None
    next_action: str | None = "理解目标并选择下一步"
    tool_calls: int = 0
    tool_call_limit: int = 8
    elapsed_seconds: int = 0
    max_duration_seconds: int = 120
    original_estimated_tokens: int = 0
    injected_estimated_tokens: int = 0
    saved_estimated_tokens: int = 0
    updated_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal_summary": self.goal_summary,
            "phase": self.phase,
            "current_action": self.current_action,
            "completed_steps": list(self.completed_steps),
            "evidence_summaries": [dict(item) for item in self.evidence_summaries],
            "active_constraints": list(self.active_constraints),
            "blocker": self.blocker,
            "next_action": self.next_action,
            "budget": {
                "tool_calls": self.tool_calls,
                "tool_call_limit": self.tool_call_limit,
                "elapsed_seconds": self.elapsed_seconds,
                "max_duration_seconds": self.max_duration_seconds,
                "original_estimated_tokens": self.original_estimated_tokens,
                "injected_estimated_tokens": self.injected_estimated_tokens,
                "saved_estimated_tokens": self.saved_estimated_tokens,
            },
            "updated_at": self.updated_at,
        }


class WorkingStateTracker:
    """把 Trace 规约成一份短小、确定且可以回放的状态快照。"""

    def __init__(
        self,
        *,
        run_id: str,
        query: str,
        tool_call_limit: int = 8,
        max_duration_seconds: int = 120,
        active_constraints: list[str] | None = None,
    ) -> None:
        self.started_at = datetime.now().astimezone()
        self.state = WorkingState(
            run_id=run_id,
            goal_summary=_compact(query, 160),
            tool_call_limit=max(1, int(tool_call_limit)),
            max_duration_seconds=max(0, int(max_duration_seconds)),
            active_constraints=[_compact(item, 160) for item in (active_constraints or []) if str(item).strip()],
        )

    def apply_trace(self, event: Any) -> dict[str, Any]:
        stage = event.stage
        if stage in {"prompt_ready", "loop_started", "reasoning"}:
            self._update(phase="preparing", current_action=event.title, next_action=event.summary)
        elif stage in {"iteration_started", "tool_started"}:
            iteration = int(event.details.get("iteration") or self.state.tool_calls + 1)
            self.state.tool_calls = min(self.state.tool_call_limit, max(self.state.tool_calls, iteration))
            self._update(phase="executing", current_action=event.title, next_action="等待工具返回并检查结果")
        elif stage in {"iteration_observed", "tool_finished", "tool_blocked"}:
            self._record_evidence(event)
            self._update(phase="evaluating", current_action=event.title, next_action="判断结果是否足以完成任务")
        elif stage == "iteration_evaluated":
            decision = str(event.details.get("decision") or "")
            next_action = "继续执行下一步" if decision == "continue" else "整理最终回复"
            self._update(phase="evaluating", current_action=event.title, next_action=next_action)
        elif stage == "waiting_input":
            self._update(
                phase="waiting_input",
                current_action="等待用户补充信息",
                blocker=event.summary,
                next_action="收到缺失信息后继续",
            )
        elif stage == "waiting_confirmation":
            self._update(
                phase="waiting_confirmation",
                current_action="等待用户确认",
                blocker=event.summary,
                next_action="确认后继续执行，取消则结束任务",
            )
        elif stage == "final_response":
            self._update(phase="responding", current_action="整理最终回复", next_action="完成并保存运行结果")
        elif stage == "citation_verification":
            self._update(phase="responding", current_action="正在核对来源", next_action="完成并保存运行结果")
        elif stage == "error" or event.status == "error":
            self._update(phase="failed", current_action=event.title, blocker=event.summary, next_action="修正问题后重试")
        return self.snapshot()

    def finish(self, *, status: str, stop_reason: str, elapsed_seconds: int | None = None) -> dict[str, Any]:
        phase = {
            "success": "completed",
            "cancelled": "cancelled",
            "needs_input": "waiting_input",
            "needs_confirmation": "waiting_confirmation",
        }.get(status, "failed")
        blocker = None if phase == "completed" else _compact(stop_reason, 240)
        next_action = None if phase in TERMINAL_PHASES else self.state.next_action
        current_action = {
            "completed": "任务已完成",
            "cancelled": "任务已取消",
            "failed": "任务执行失败",
            "waiting_input": "等待用户补充信息",
            "waiting_confirmation": "等待用户确认",
        }[phase]
        self._update(phase=phase, current_action=current_action, blocker=blocker, next_action=next_action)
        if elapsed_seconds is not None:
            self.state.elapsed_seconds = max(0, int(elapsed_seconds))
        return self.snapshot()

    def add_token_usage(self, *, original: int, injected: int) -> dict[str, Any]:
        self.state.original_estimated_tokens += max(0, int(original))
        self.state.injected_estimated_tokens += max(0, int(injected))
        self.state.saved_estimated_tokens = max(
            0,
            self.state.original_estimated_tokens - self.state.injected_estimated_tokens,
        )
        self._touch()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        elapsed = int((datetime.now().astimezone() - self.started_at).total_seconds())
        self.state.elapsed_seconds = max(self.state.elapsed_seconds, elapsed)
        return self.state.to_dict()

    def _record_evidence(self, event: Any) -> None:
        summary = _compact(
            event.details.get("output_summary")
            or event.details.get("result")
            or event.summary,
            240,
        )
        item = {
            "tool": event.details.get("tool"),
            "status": event.status,
            "summary": summary,
            "evidence_id": event.details.get("evidence_id"),
        }
        if item not in self.state.evidence_summaries:
            self.state.evidence_summaries.append(item)
            self.state.evidence_summaries = self.state.evidence_summaries[-8:]
        if event.status == "success":
            step = _compact(event.title, 160)
            if step and step not in self.state.completed_steps:
                self.state.completed_steps.append(step)
                self.state.completed_steps = self.state.completed_steps[-8:]

    def _update(
        self,
        *,
        phase: str,
        current_action: str,
        blocker: str | None = None,
        next_action: str | None = None,
    ) -> None:
        self.state.phase = phase
        self.state.current_action = _compact(current_action, 160)
        self.state.blocker = _compact(blocker, 240) if blocker else None
        self.state.next_action = _compact(next_action, 200) if next_action else None
        self._touch()

    def _touch(self) -> None:
        self.state.updated_at = datetime.now().astimezone().isoformat()


def rebuild_working_state(
    *,
    run_id: str,
    query: str,
    status: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """从持久化的公开事件恢复状态，供刷新和旧进程中断后的查询使用。"""
    tracker = WorkingStateTracker(run_id=run_id, query=query)
    for event in events:
        if event.get("type") == "status_updated" and isinstance(event.get("payload", {}).get("state"), dict):
            return dict(event["payload"]["state"])
    return tracker.finish(status=status, stop_reason=_status_reason(status))


def render_working_state_instruction(state: dict[str, Any] | None) -> str:
    """渲染给模型的短状态，不包含原始 Tool 输出或内部推理。"""
    if not state:
        return ""
    completed = [str(item) for item in state.get("completed_steps") or []][-4:]
    evidence = [str(item.get("summary") or "") for item in state.get("evidence_summaries") or []][-4:]
    lines = [
        "当前任务工作状态：",
        f"- 目标：{state.get('goal_summary') or '未记录'}",
        f"- 阶段：{state.get('phase') or 'preparing'}",
        f"- 当前动作：{state.get('current_action') or '未记录'}",
    ]
    if completed:
        lines.append("- 已完成：" + "；".join(completed))
    if evidence:
        lines.append("- 已有证据：" + "；".join(evidence))
    if state.get("blocker"):
        lines.append(f"- 阻塞：{state['blocker']}")
    if state.get("next_action"):
        lines.append(f"- 下一步：{state['next_action']}")
    lines.append("只把以上内容当作任务状态；需要原始证据时应读取对应证据，不得据此编造细节。")
    return "\n".join(lines)


def load_latest_session_working_state(
    *,
    user_id: str,
    session_id: str,
    exclude_run_id: str | None = None,
) -> dict[str, Any] | None:
    """读取同会话上一轮终态；读取失败时不影响 Agent 主链路。"""
    try:
        store = MySQLMemoryStore()
        conditions = ["user_id = %s", "session_id = %s", "metadata_json IS NOT NULL"]
        params: list[Any] = [user_id, session_id]
        if exclude_run_id:
            conditions.append("run_id <> %s")
            params.append(exclude_run_id)
        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT metadata_json
                    FROM agent_runs
                    WHERE {' AND '.join(conditions)}
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    tuple(params),
                )
                row = cursor.fetchone()
    except Exception:
        return None
    if not row:
        return None
    metadata = row.get("metadata_json")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            return None
    state = metadata.get("working_state") if isinstance(metadata, dict) else None
    return dict(state) if isinstance(state, dict) else None


def _status_reason(status: str) -> str:
    return {
        "success": "任务完成",
        "cancelled": "用户取消任务",
        "needs_input": "等待用户补充信息",
        "needs_confirmation": "等待用户确认",
    }.get(status, "任务执行失败")


def _compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"
