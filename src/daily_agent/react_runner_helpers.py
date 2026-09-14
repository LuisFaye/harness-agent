"""AgentLoopRunner 的纯辅助函数，避免主模块超过单文件行数限制。"""

from __future__ import annotations

import json
import math
from typing import Any

from daily_agent.executor import StepObservation
from daily_agent.planner import PlanStep, TaskPlan
from daily_agent.runtime_state import ReactPauseStateRecord
from daily_agent.react_text import (
    coerce_text as _coerce_text,
    friendly_tool_name as _friendly_tool_name,
)
from daily_agent.react_runner_models import PendingReactRun
from daily_agent.tool_progress import business_arguments


def pending_run_from_record(record: ReactPauseStateRecord) -> PendingReactRun | None:
    return pending_run_from_payload(
        payload=record.payload,
        run_id=record.run_id,
        status=record.status,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
    )


def pending_run_from_payload(
    *,
    payload: dict[str, Any],
    run_id: str,
    status: str,
    updated_at: str | None,
    expires_at: str | None,
) -> PendingReactRun | None:
    if not isinstance(payload, dict):
        return None
    try:
        from agno.run.agent import RunOutput

        run_output = RunOutput.from_dict(json.loads(json.dumps(payload)))
    except Exception:
        return None
    return PendingReactRun(
        run_id=run_id,
        status=status,
        run_output=run_output,
        updated_at=updated_at,
        expires_at=expires_at,
    )


def synthesize_plan(observations: list[StepObservation]) -> TaskPlan:
    if observations:
        steps = [
            PlanStep(
                name=_friendly_tool_name(item.tool or item.name),
                tool=item.tool,
                reason="模型在推理过程中自主选择并调用了这个工具。",
            )
            for item in observations
        ]
    else:
        steps = [
            PlanStep(
                name="直接回答用户问题",
                tool=None,
                reason="模型判断不需要调用实时工具，直接回答。",
            )
        ]
    return TaskPlan(intent="agent_loop", steps=steps, missing_info=[], risk_level="low")


def confirm_run_requirements(run_output: Any) -> None:
    requirements = getattr(run_output, "requirements", None) or []
    for requirement in requirements:
        try:
            if getattr(requirement, "needs_confirmation", False):
                requirement.confirm()
        except Exception:
            continue
    for tool in getattr(run_output, "tools", None) or []:
        if getattr(tool, "requires_confirmation", False):
            tool.confirmed = True


def confirmation_tool_names(run_output: Any) -> set[str]:
    names: set[str] = set()
    for requirement in getattr(run_output, "requirements", None) or []:
        tool = getattr(requirement, "tool_execution", None) or getattr(requirement, "tool", None)
        name = tool_name(tool) if tool is not None else ""
        if name and name != "tool":
            names.add(name.replace("-", "_"))
    for tool in getattr(run_output, "tools", None) or []:
        if getattr(tool, "requires_confirmation", False):
            names.add(tool_name(tool).replace("-", "_"))
    return names


def is_paused_run_output(value: Any) -> bool:
    status = str(getattr(value, "status", "") or "").lower()
    return "paused" in status or bool(getattr(value, "is_paused", False))


def is_run_output_object(value: Any) -> bool:
    """判断事件流里 yield 出来的 RunOutput 对象（区别于各类流式事件）。"""
    return hasattr(value, "to_dict") and hasattr(value, "tools")


# provider_metrics 里的供应商缓存键 → 统一 usage 键。
# 这里只映射“明确上报”的缓存字段：0 也必须保留，不能当“未上报”。
_PROVIDER_CACHE_USAGE_KEYS = {
    "prompt_cache_hit_tokens": "cache_read_tokens",
    "prompt_cache_miss_tokens": "cache_miss_tokens",
}


def _usage_int(value: Any) -> int:
    """日志路径不能抛异常，无法解析的值一律归零。"""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def format_cache_usage(usage: dict[str, Any]) -> str:
    """把供应商上报的 KV cache 命中情况压成一行日志。

    该指标可用于观察前缀稳定性，但也会受模型供应商缓存策略等因素影响，
    不能单独作为性能结论。
    """
    read = _usage_int(usage.get("cache_read_tokens"))
    miss = _usage_int(usage.get("cache_miss_tokens"))
    total = read + miss
    if not total:
        return "cache_read=0 cache_miss=0 cache_hit_rate=n/a"
    return (
        f"cache_read={read} cache_miss={miss} "
        f"cache_hit_rate={100 * read / total:.1f}%"
    )


def _collect_provider_cache_metrics(metrics: Any) -> dict[str, int]:
    """从 RunOutput.metrics.details 各模型条目汇总明确上报的缓存 hit/miss。

    Agno 的 ModelMetrics.accumulate 会合并 provider_metrics（数值求和），
    因此 RunOutput.metrics.details[*].provider_metrics 保留的是供应商明确
    上报的字段，包括 0。遍历所有模型类型与条目，把 DeepSeek 的
    prompt_cache_hit_tokens / prompt_cache_miss_tokens 累加到统一 usage 键。
    条目可能是 ModelMetrics 对象，也可能是反序列化后的 dict，两种情况都兼容。
    """
    collected: dict[str, int] = {}
    details = getattr(metrics, "details", None)
    if not isinstance(details, dict):
        return collected
    for entries in details.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            provider_metrics = (
                entry.get("provider_metrics")
                if isinstance(entry, dict)
                else getattr(entry, "provider_metrics", None)
            )
            if not isinstance(provider_metrics, dict):
                continue
            for provider_key, usage_key in _PROVIDER_CACHE_USAGE_KEYS.items():
                raw = provider_metrics.get(provider_key)
                # 只接受非负有限数值；布尔、负数、NaN 等异常值视为未上报。
                if (
                    raw is None
                    or isinstance(raw, bool)
                    or not isinstance(raw, (int, float))
                    or not math.isfinite(raw)
                    or raw < 0
                ):
                    continue
                collected[usage_key] = collected.get(usage_key, 0) + int(raw)
    return collected


def accumulate_usage_from_run_output(usage: dict[str, Any], run_output: Any) -> None:
    """把 RunOutput.metrics 中供应商实际上报的 token/cache 指标累加到 usage。

    字段名与 RunUsage / 前端 runUsageSchema 保持一致；缺失时保持 null（不写 0），
    这样前端才能区分「未上报」和「上报为 0」。metrics 对象各字段可能为 None。
    usage 由调用方跨外层重试共享，同一字典会依次累加多次真实模型请求，因此
    这里绝不能因为「usage 中已有历史值」就跳过标准字段的本次累加。

    优先级：
    1. 先汇总 details[*].provider_metrics 中明确上报的缓存命中/未命中
       Token（包括 0），这两个键一旦写入，后面的正数兜底不会再覆盖；
    2. 其余标准字段（input/output/total/cache_write/reasoning）以及没有
       provider 明确上报时的 cache_read，仍沿用旧的“可靠正数”兜底，
       避免把 Agno dataclass 默认 0 误当成供应商上报；跳过条件只看
       「当前 RunOutput 是否已由 provider_metrics 明确提供该键」，
       与共享 usage 中的历史值无关。
    """
    if run_output is None:
        return
    metrics = getattr(run_output, "metrics", None)
    if metrics is None:
        return
    # 第一步：明确上报的 hit/miss 优先持久化，0 值也保留。
    provider_cache_metrics = _collect_provider_cache_metrics(metrics)
    for usage_key, value in provider_cache_metrics.items():
        usage[usage_key] = usage.get(usage_key, 0) + value
    # 第二步：标准字段的正数兜底。只跳过“当前 RunOutput 已由 provider_metrics
    # 明确提供”的键（主要是本次 hit/miss）；共享 usage 中的历史值来自上一次
    # 模型请求，不能作为跳过本次累加的依据，否则跨重试统计会偏小。
    explicitly_reported_keys = set(provider_cache_metrics)
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    ):
        if key in explicitly_reported_keys:
            continue
        value = getattr(metrics, key, None)
        if isinstance(value, (int, float)) and value > 0:
            usage[key] = usage.get(key, 0) + int(value)


def observation_from_tool(*, index: int, tool: Any, duration_ms: int) -> StepObservation:
    tool_name_value = tool_name(tool)
    error = getattr(tool, "tool_call_error", None)
    result = getattr(tool, "result", None)
    status = "error" if error else "success"
    result_text = _coerce_text(result) if result is not None else None
    error_text = _coerce_text(error) if error else None
    if error and isinstance(error, bool) and result_text:
        error_text = result_text
    return StepObservation(
        index=index,
        name=_friendly_tool_name(tool_name_value),
        tool=tool_name_value,
        status=status,
        inputs=tool_args(tool),
        output=None if error else result_text,
        error_message=error_text,
        duration_ms=duration_ms,
        attempts=1,
    )


def response_acknowledges_tool_failure(response_text: str) -> bool:
    """防止模型在工具失败后仍把旧结果包装成确定结论。"""
    text = str(response_text or "").lower()
    markers = ("超时", "失败", "错误", "无法", "不可用", "未成功", "中断", "timeout", "failed", "error")
    return any(marker in text for marker in markers)


def tool_name(tool: Any) -> str:
    return str(getattr(tool, "tool_name", "") or "tool")


def tool_args(tool: Any) -> dict[str, Any]:
    args = getattr(tool, "tool_args", None)
    if isinstance(args, dict):
        return dict(args)
    return {}


def tool_key(tool: Any) -> str:
    return str(getattr(tool, "tool_call_id", None) or tool_name(tool))


def tool_event_signature(tool: Any) -> str:
    """Agno 有时会复用上次失败结果，因此事件层也要按工具名和参数去重。"""
    arguments = json.dumps(business_arguments(tool_args(tool)), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return f"{tool_name(tool)}:{arguments}"


def confirmation_note(event: Any) -> str:
    tools = list(getattr(event, "tools", None) or [])
    for requirement in getattr(event, "requirements", None) or []:
        tool = getattr(requirement, "tool_execution", None)
        if tool is not None:
            tools.append(tool)
    pending = [tool for tool in tools if getattr(tool, "requires_confirmation", False)
               and not getattr(tool, "confirmed", False)]
    if pending:
        from daily_agent.shell_security import redact_command

        descriptions = []
        for tool in pending:
            name = tool_name(tool)
            arguments = tool_args(tool)
            description = f"- {name}"
            if name == "shell_execute":
                command = str(arguments.get("command") or "")
                description += f"\n  命令：{redact_command(command)}"
                description += f"\n  目录：{arguments.get('cwd') or '.'}"
            if description not in descriptions:
                descriptions.append(description)
        return "需要确认以下操作（仅授权本次列出的调用，后续操作仍单独判断）：\n" + "\n".join(descriptions)
    fallback = "这个操作需要你先确认。回复“确认”后我再继续，回复“取消”可以终止。"
    requirements = getattr(event, "requirements", None)
    if isinstance(requirements, list):
        for item in requirements:
            note = getattr(item, "confirmation_note", None)
            if note:
                text = str(note)
                if "confirm" not in text.lower():
                    return text
    content = _coerce_text(getattr(event, "content", None))
    if content and "confirm" not in content.lower():
        return content
    return fallback
