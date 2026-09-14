"""把有自定义逻辑的 skill 适配成 Agno 工具，接入同一个 ReAct 循环。

设计要点
--------
- 旧链路里 skill 由 SkillGateway 关键词路由命中后单独执行；现在让模型在工具调用
  循环里自主决定「要不要用这个 skill」。
- 每个「可执行 + 已启用」的 skill，用它的 `input_schema` 作为工具参数 schema，
  用 `tool_name` 作为工具名，闭包内调 SkillRuntime 执行对应 handler。
- 核心 Agent 不再内置领域工具；所有可执行能力都来自启用的 skill。
"""

from __future__ import annotations

from typing import Any

from daily_agent.skill_catalog import SkillRecord, list_enabled_skill_records
from daily_agent.skill_runtime import SKILL_STATUS_ERROR, SkillRuntime
from daily_agent.tool_registry import ToolRegistry, skill_tool_id


def build_skill_tools(
    *,
    user_id: str,
    session_id: str,
    run_id: str | None = None,
    skill_records: list[SkillRecord] | None = None,
    runtime: SkillRuntime | None = None,
    tool_registry: ToolRegistry | None = None,
    confirmed_tool_names: set[str] | None = None,
) -> list[Any]:
    """把当前启用的可执行 skill 转成 Agno Function 工具列表。"""
    try:
        from agno.tools.function import Function
    except ImportError:  # pragma: no cover - agno 缺失时由上层处理
        return []

    records = skill_records if skill_records is not None else list_enabled_skill_records()
    runtime = runtime or SkillRuntime()
    tool_registry = tool_registry or ToolRegistry()
    confirmed_names = {str(item).replace("-", "_") for item in (confirmed_tool_names or set())}
    tools: list[Any] = []
    for skill in records:
        if not skill.execution:
            continue
        if skill.interaction_mode != "react_tool":
            continue
        if not tool_registry.is_tool_enabled(skill_tool_id(skill)):
            continue
        tool_name = (skill.tool_name or skill.name).replace("-", "_")
        tools.append(
            Function(
                name=tool_name,
                description=_build_tool_description(skill),
                parameters=_normalize_parameters(skill.input_schema),
                entrypoint=_make_entrypoint(skill, runtime, user_id=user_id, session_id=session_id, run_id=run_id),
                skip_entrypoint_processing=True,
                requires_confirmation=skill.requires_confirmation and tool_name not in confirmed_names,
            )
        )
    return tools


def _make_entrypoint(
    skill: SkillRecord,
    runtime: SkillRuntime,
    *,
    user_id: str,
    session_id: str,
    run_id: str | None = None,
):
    def _entrypoint(**kwargs: Any) -> str:
        # Agno 可能注入 agent/session_state 等非业务参数，这里只保留 schema 里声明的入参。
        inputs = _filter_inputs(skill, kwargs)
        result = runtime.run(
            skill=skill,
            query=str(kwargs.get("query") or kwargs.get("task") or ""),
            user_id=user_id,
            session_id=session_id,
            inputs=inputs,
            state={"mode": "tool"},
            run_id=run_id,
        )
        if getattr(result, "status", None) == SKILL_STATUS_ERROR:
            raise RuntimeError(result.response_text or "技能工具执行失败")
        return result.output_text or result.response_text or "（这个技能没有返回内容）"

    _entrypoint.__name__ = (skill.tool_name or skill.name).replace("-", "_")
    return _entrypoint


def _filter_inputs(skill: SkillRecord, kwargs: dict[str, Any]) -> dict[str, Any]:
    schema = skill.input_schema if isinstance(skill.input_schema, dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    if not properties:
        return {key: value for key, value in kwargs.items() if key != "query"}
    return {key: kwargs[key] for key in properties if key in kwargs}


def _normalize_parameters(input_schema: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(input_schema, dict) and input_schema.get("type") == "object":
        return input_schema
    return {"type": "object", "properties": {}}


def _build_tool_description(skill: SkillRecord) -> str:
    parts = [skill.description or f"{skill.name} skill"]
    execution_type = str((skill.execution or {}).get("type") or "")
    if execution_type == "compatible_skill":
        parts.append(
            "这是第三方兼容 skill。需要执行浏览器或命令行步骤时，优先在 inputs.command 中传入一条具体的白名单命令，"
            "例如 agent-browser open https://example.com、agent-browser snapshot -i、agent-browser click @e1。"
        )
    if skill.intent_examples:
        parts.append("适用示例：" + "；".join(skill.intent_examples[:3]))
    return " ".join(parts)
