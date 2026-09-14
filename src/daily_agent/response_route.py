"""为 ask 主链路选择执行模式。"""

from __future__ import annotations


def should_use_fast_response(query: str) -> bool:
    """是否使用无工具的轻量快答链路。

    当前已统一关闭快答：短句、确认词（如「可以」「？」）在上下文里常代表
    继续执行上一轮任务，快答禁工具时容易编造文件或执行结果。
    """
    del query
    return False
