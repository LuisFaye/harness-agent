"""在 SDK 判断是否暂停确认之前校验模型参数，不执行无效工具请求。"""

from __future__ import annotations

from typing import Any

from agno.tools.function import Function
from jsonschema import Draft202012Validator


def validate_tool_calls(calls: list[Any]) -> list[Any]:
    """无效请求作为普通工具错误回给模型修正，仍计入 SDK 的工具次数限制。"""
    for call in calls:
        schema = call.function.parameters
        if not isinstance(schema, dict):
            continue
        error = next(Draft202012Validator(schema).iter_errors(call.arguments or {}), None)
        if error is None:
            continue
        path = ".".join(str(part) for part in error.absolute_path) or "参数"
        required = schema.get("required", [])
        # 只返回字段名和规则，不把命令、凭据或其他参数原值写进错误信息。
        message = (
            f"工具参数无效：{path} 未通过 {error.validator} 校验。"
            f"必填字段：{', '.join(str(item) for item in required) or '无'}。"
            "本次没有执行任何操作，也不需要用户确认。请修正参数；"
            "如该工具不是任务所需，请直接利用已有结果回答，不要重复相同错误调用。"
        )
        call.function = Function(
            name=call.function.name,
            entrypoint=_reject_call(message),
            parameters=schema,
            skip_entrypoint_processing=True,
        )
    return calls


def _reject_call(message: str):
    """替代入口只返回校验失败，绝不触达原工具或绕过其执行权限。"""
    def reject(**kwargs: Any) -> str:
        raise ValueError(message)
    return reject
