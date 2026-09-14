"""技能执行运行时。

核心目标只有两个：
1. 主程序不再为每个 skill 写 `if skill == xxx`；
2. skill 自己负责参数解析和业务执行，Runtime 只负责统一装配上下文与调度。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Callable

from daily_agent.errors import DailyAgentError
from daily_agent.factories import StoreFactory
from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_isolation import SkillIsolationPaths, prepare_skill_isolation
from daily_agent.skill_memory import MySQLSkillPreferenceRepository, SkillPreferenceRepository, SkillPrivateMemory
from daily_agent.user_workspace import user_workspace_root

SKILL_STATUS_SUCCESS = "success"
SKILL_STATUS_NEEDS_INPUT = "needs_input"
SKILL_STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
SKILL_STATUS_ERROR = "error"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillExecutionContext:
    """传给 skill handler 的上下文。

    handler 能拿到：
    - 原始 query
    - 命中的 skill 元数据
    - 当前 ask 会话中的已收集输入
    - handler 自己的上一步状态
    - 当前工作目录，方便默认按当前项目处理
    """

    query: str
    user_id: str
    session_id: str
    skill: SkillRecord
    working_directory: str
    run_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    private_memory: SkillPrivateMemory | None = None
    isolation: SkillIsolationPaths | None = None


@dataclass(frozen=True)
class SkillRunResult:
    """统一的 skill handler 返回结构。"""

    status: str
    response_text: str
    stop_reason: str
    inputs: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    missing_info: list[str] = field(default_factory=list)
    output_text: str | None = None
    observation: dict[str, Any] = field(default_factory=dict)
    clear_session: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SkillRunResult":
        return cls(
            status=str(payload.get("status") or SKILL_STATUS_ERROR),
            response_text=str(payload.get("response_text") or ""),
            stop_reason=str(payload.get("stop_reason") or "技能执行完成"),
            inputs=dict(payload.get("inputs") or {}),
            state=dict(payload.get("state") or {}),
            missing_info=[str(item) for item in (payload.get("missing_info") or [])],
            output_text=str(payload.get("output_text")) if payload.get("output_text") is not None else None,
            observation=dict(payload.get("observation") or {}),
            clear_session=bool(payload.get("clear_session", False)),
        )


class SkillRuntime:
    """统一执行 skill handler。"""

    def __init__(
        self,
        *,
        working_directory: str | None = None,
        store_factory: StoreFactory | None = None,
        skill_memory_repository: SkillPreferenceRepository | None = None,
        isolation_root: str | None = None,
    ) -> None:
        self.working_directory = working_directory
        self.store_factory = store_factory
        self.skill_memory_repository = skill_memory_repository
        self.isolation_root = isolation_root

    def run(
        self,
        *,
        skill: SkillRecord,
        query: str,
        user_id: str,
        session_id: str,
        inputs: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> SkillRunResult:
        execution = skill.execution or {}
        working_directory = self.working_directory
        if not working_directory or working_directory == ".":
            working_directory = str(user_workspace_root(user_id))
        execution_type = str(execution.get("type") or "").strip()
        memory_repository = self.skill_memory_repository
        if memory_repository is None and self.store_factory is not None:
            memory_repository = MySQLSkillPreferenceRepository(self.store_factory)
        isolation = prepare_skill_isolation(
            skill_name=skill.name,
            skill_directory=skill.path,
            user_id=user_id,
            run_id=run_id,
            runtime_root=self.isolation_root,
        )
        context = SkillExecutionContext(
            query=query,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            skill=skill,
            working_directory=working_directory,
            inputs=dict(inputs or {}),
            state=dict(state or {}),
            private_memory=(
                SkillPrivateMemory(user_id=user_id, skill_name=skill.name, repository=memory_repository)
                if memory_repository is not None
                else None
            ),
            isolation=isolation,
        )
        started_at = time.perf_counter()
        try:
            logger.info("skill runtime started: skill=%s tool=%s", skill.name, skill.tool_name or skill.name)
            if execution_type == "python_callable":
                result = self._run_python_callable(execution=execution, context=context)
            elif execution_type == "compatible_skill":
                result = self._run_compatible_skill(context=context)
            else:
                raise DailyAgentError(f"暂不支持的 skill 执行类型：{execution_type or '未配置'}")
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info("skill runtime finished: skill=%s status=%s duration_ms=%s", skill.name, result.status, duration_ms)
            _safe_add_tool_log(
                store_factory=self.store_factory,
                user_id=user_id,
                tool_name=skill.tool_name or skill.name,
                inputs=context.inputs,
                output_summary=_compact_text(result.output_text or result.response_text, 1000),
                status=result.status,
                duration_ms=duration_ms,
                error_message=result.response_text if result.status == SKILL_STATUS_ERROR else None,
                run_id=run_id,
                source_skill=skill.name,
                risk_level=skill.risk_level,
                requires_confirmation=skill.requires_confirmation,
                confirmed=bool(context.state.get("confirmation_received")),
                failure_stage="skill_runtime" if result.status == SKILL_STATUS_ERROR else None,
            )
            return result
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            logger.exception("skill runtime failed: skill=%s duration_ms=%s", skill.name, duration_ms)
            _safe_add_tool_log(
                store_factory=self.store_factory,
                user_id=user_id,
                tool_name=skill.tool_name or skill.name,
                inputs=context.inputs,
                output_summary=None,
                status=SKILL_STATUS_ERROR,
                duration_ms=duration_ms,
                error_message=str(exc),
                run_id=run_id,
                source_skill=skill.name,
                risk_level=skill.risk_level,
                requires_confirmation=skill.requires_confirmation,
                confirmed=bool(context.state.get("confirmation_received")),
                failure_stage="skill_runtime",
            )
            return SkillRunResult(
                status=SKILL_STATUS_ERROR,
                response_text=f"技能 {skill.name} 执行失败：{exc}",
                stop_reason="技能运行时失败",
                inputs=context.inputs,
                state=context.state,
                output_text=None,
                observation={"error": str(exc)},
                clear_session=True,
            )
        finally:
            isolation.cleanup_temporary()

    def _run_python_callable(self, *, execution: dict[str, Any], context: SkillExecutionContext) -> SkillRunResult:
        entrypoint = str(execution.get("entrypoint") or "").strip()
        if ":" not in entrypoint:
            raise DailyAgentError(f"skill entrypoint 配置无效：{entrypoint or '空'}")

        handler = self._load_callable(entrypoint)
        result = handler(context=context)
        if isinstance(result, SkillRunResult):
            return result
        if isinstance(result, dict):
            return SkillRunResult.from_payload(result)
        raise DailyAgentError(f"skill handler 返回值不受支持：{type(result).__name__}")

    def _run_compatible_skill(self, *, context: SkillExecutionContext) -> SkillRunResult:
        from daily_agent.compatible_skill_runtime import CompatibleSkillRuntime

        return CompatibleSkillRuntime().run(context=context)

    def _load_callable(self, entrypoint: str) -> Callable[..., Any]:
        module_name, attr_name = entrypoint.split(":", 1)
        try:
            module = import_module(module_name)
        except Exception as exc:
            raise DailyAgentError(f"加载 skill 模块失败：{module_name} - {exc}") from exc
        handler = getattr(module, attr_name, None)
        if not callable(handler):
            raise DailyAgentError(f"skill handler 不存在或不可调用：{entrypoint}")
        return handler


def _safe_add_tool_log(
    *,
    store_factory: StoreFactory | None,
    user_id: str,
    tool_name: str,
    inputs: dict[str, Any],
    output_summary: str | None,
    status: str,
    duration_ms: int,
    error_message: str | None = None,
    run_id: str | None = None,
    source_skill: str | None = None,
    risk_level: str | None = None,
    requires_confirmation: bool | None = None,
    confirmed: bool | None = None,
    failure_stage: str | None = None,
) -> None:
    try:
        if store_factory is None:
            return
        store_factory().add_tool_call_log(
            tool_name=tool_name,
            inputs=inputs,
            output_summary=output_summary,
            status=status,
            duration_ms=duration_ms,
            error_message=error_message,
            user_id=user_id,
            run_id=run_id,
            source_skill=source_skill,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            confirmed=confirmed,
            failure_stage=failure_stage,
        )
    except DailyAgentError:
        pass


def _compact_text(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "...（已截断）"
