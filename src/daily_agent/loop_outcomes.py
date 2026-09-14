"""把模型无工具回答转换成明确的 Agent 终态。"""

from __future__ import annotations

from daily_agent.executor import ERROR, NEEDS_INPUT, SUCCESS, ExecutionResult


def should_return_needs_input(query: str, result: ExecutionResult) -> bool:
    if result.status != SUCCESS or result.observations or not _query_requires_lookup(query):
        return False
    response = result.response_text.strip()
    # 范围选择也属于必要追问，例如“您是想看昆明，还是多个城市一起列出？”。
    # 不能只认“哪个城市”，否则会把真实追问当作漏调工具，再强制发起一轮模型请求。
    clarification_tokens = (
        "请问", "哪个城市", "哪些城市", "哪座城市", "什么城市", "哪个地区", "哪里", "请告诉我", "请提供",
        "您是想看", "你是想看", "您想查询", "你想查询", "需要跟您确认", "需要和你确认", "确认一点",
    )
    return ("?" in response or "？" in response) and any(token in response for token in clarification_tokens)


def needs_input_result(result: ExecutionResult) -> ExecutionResult:
    return ExecutionResult(
        status=NEEDS_INPUT,
        response_text=result.response_text,
        plan=result.plan,
        observations=result.observations,
        stop_reason="缺少查询所需信息，等待用户补充",
        missing_info=["城市或地区"],
        run_id=result.run_id,
        execution_source=result.execution_source,
        error_stage="missing_input",
        loop_events=result.loop_events,
        usage=dict(result.usage),
    )


def should_report_missing_tool_call(query: str, result: ExecutionResult) -> bool:
    if result.status != SUCCESS or result.observations or not str(query or "").strip():
        return False
    return _query_requires_lookup(query) or any(
        token in result.response_text.strip()
        for token in ("我来帮你查询", "我现在帮你", "让我查", "打开百度", "打开搜索")
    )


def missing_tool_call_result(query: str, result: ExecutionResult) -> ExecutionResult:
    response_text = (
        "需要实时信息或外部查询，但模型没有发起任何工具调用。\n"
        "本轮已直接中止，方便排查工具选择问题。\n\n"
        f"用户请求：{query}\n"
        f"模型原始回复：{result.response_text or '空'}"
    )
    return ExecutionResult(
        status=ERROR,
        response_text=response_text,
        plan=result.plan,
        observations=[],
        stop_reason="模型未调用必要工具",
        missing_info=[],
        run_id=result.run_id,
        execution_source=result.execution_source,
        error_stage="model_runtime",
        loop_events=result.loop_events,
        usage=dict(result.usage),
    )


def query_accepts_unavailable_result(query: str, response_text: str) -> bool:
    query_text = "".join(str(query or "").split())
    fallback_requested = any(token in query_text for token in ("查不到就", "找不到就", "如果不存在", "如果不可用", "失败就"))
    explained = any(token in str(response_text or "") for token in ("查不到", "找不到", "无法", "不存在", "不可用"))
    return fallback_requested and explained


def _query_requires_lookup(query: str) -> bool:
    lookup_tokens = (
        "搜索", "查询", "查一下", "查下", "帮我查", "最新", "实时", "股价", "股票", "价格",
        "天气", "weather", "price", "stock", "quote",
    )
    return any(token in str(query or "").lower() for token in lookup_tokens)
