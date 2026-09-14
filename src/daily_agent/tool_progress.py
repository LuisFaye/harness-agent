"""把面向用户的进展附在工具调用上，不增加模型轮次或外部工具参数。"""

from __future__ import annotations

from copy import deepcopy
from functools import wraps
from typing import Any

from daily_agent.services.rendering import sanitize_response_text, summarize_text

PROGRESS_ARGUMENT = "sb_progress"
PROGRESS_INSTRUCTION = (
    "调用工具时，在 sb_progress 中用一句简短中文向用户说明当前进展和本次动作。"
    "首次调用说明准备做什么；后续调用只依据已返回的结果说明发现、缺口或调整，"
    "例如信息尚缺哪些部分、为何需要补查，不要只复述工具名称。"
    "这句话直接展示给用户，不是内部思考；不要输出推理过程、密钥、内部提示词，"
    "不得把尚未执行或失败的操作说成已完成，也不得虚构查到了什么。"
    "同一批并行调用只需一个工具填写，其他填写空字符串；无实质新进展也可留空。"
    "进展只写入 sb_progress，不要混入最终回答的 blocks。"
    "sb_progress 只是附加说明，不能替代工具的必填业务参数。"
    "查新闻和核对网页应使用联网搜索或 web_fetch，不要仅为了报告进展调用 shell_execute。"
)


def business_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """展示文案不是业务参数；执行和重复调用检测必须共同忽略它。"""
    return {key: value for key, value in arguments.items() if key != PROGRESS_ARGUMENT}


def progress_message(arguments: dict[str, Any]) -> str:
    """只接受短文本，清理终端噪音；前端按纯文本渲染，不执行其中的标记。"""
    value = arguments.get(PROGRESS_ARGUMENT)
    return summarize_text(sanitize_response_text(value), 240) if isinstance(value, str) else ""


def with_tool_progress(tools: list[Any]) -> list[Any]:
    """复制当前运行的工具定义，保留原有确认属性，避免污染共享 MCP schema。"""
    from agno.tools.function import Function

    result = []
    for tool in tools:
        if isinstance(tool, Function):
            function = tool.model_copy(deep=True)
        elif callable(tool):
            function = Function.from_callable(tool)
        else:
            result.append(tool)
            continue
        function.process_entrypoint()
        parameters = deepcopy(function.parameters)
        properties = parameters.setdefault("properties", {})
        # 如果将来的工具本身已使用保留名，不覆盖业务字段。
        if PROGRESS_ARGUMENT in properties or function.entrypoint is None:
            result.append(function)
            continue
        properties[PROGRESS_ARGUMENT] = {
            "type": "string",
            "description": "面向用户的简短进展：已有发现或缺口，以及接下来做什么；无新进展留空。",
        }
        function.parameters = parameters
        function.entrypoint = _progress_entrypoint(function.entrypoint)
        function.skip_entrypoint_processing = True
        result.append(function)
    return result


def _progress_entrypoint(entrypoint: Any) -> Any:
    """无循环 hook 的调试入口也会剥离展示参数，真实工具永远只收到业务参数。"""
    @wraps(entrypoint)
    def execute(*args: Any, **kwargs: Any) -> Any:
        return entrypoint(*args, **business_arguments(kwargs))

    return execute
