"""Ask 终态结果的序列化辅助，避免 api.py 超过单文件行数限制。"""

from __future__ import annotations

from typing import Any

from daily_agent.model_request_store import save_model_requests


def serialize_ask_result(result: Any) -> dict[str, Any]:
    """把 AskServiceResult 序列化为流式终态 payload，供前端立即更新。

    完整模型请求体写入本地短命存档，不放进返回 dict，避免再进 MySQL metadata。
    """
    run_id = getattr(result.execution, "run_id", None)
    model_requests = list(getattr(result.execution, "model_requests", None) or [])
    if run_id and model_requests:
        save_model_requests(
            str(run_id),
            model_requests,
            user_id=str(getattr(result, "user_id", "") or "") or None,
        )
    return {
        "run_id": result.execution.run_id,
        "status": result.execution.status,
        "execution_source": getattr(result.execution, "execution_source", None),
        "error_stage": getattr(result.execution, "error_stage", None),
        "usage": dict(getattr(result.execution, "usage", {}) or {}),
        "response_text": result.response_text,
        "stop_reason": result.execution.stop_reason,
        "missing_info": result.execution.missing_info,
        "plan": result.plan.to_metadata(),
        "observations": [item.to_metadata() for item in result.execution.observations],
        "extracted_memories": [item.__dict__ for item in result.extracted_memories],
        "trace": [item.to_metadata() for item in result.trace],
        "prompt_snapshot": result.prompt_snapshot,
        "working_state": getattr(result.execution, "working_state", None),
        "interaction": getattr(result.execution, "interaction", None),
        "citation_bundle": (
            result.execution.citation_bundle.model_dump(mode="json")
            if getattr(result.execution, "citation_bundle", None)
            else None
        ),
    }


def sanitize_member_result(result: dict[str, Any]) -> dict[str, Any]:
    """普通成员可查看脱敏后的执行过程，但不返回内部 Prompt、模型请求和记忆抽取结果。"""
    sanitized = dict(result)
    sanitized["prompt_snapshot"] = None
    sanitized["model_requests"] = []
    sanitized["extracted_memories"] = []
    return sanitized


def secure_input_result(run_id: str, interaction: dict) -> dict:
    """构造不经过模型的安全输入终态，确保误粘贴密码不会进入 Prompt 或会话历史。"""
    return {
        "run_id": run_id,
        "status": "needs_input",
        "execution_source": "local",
        "error_stage": "secure_input",
        "response_text": "检测到数据库凭据。请在下方安全表单中重新填写密码；它不会发送给模型。",
        "stop_reason": "等待用户安全填写数据库连接",
        "missing_info": [],
        "plan": None,
        "observations": [],
        "extracted_memories": [],
        "trace": [],
        "prompt_snapshot": None,
        "model_requests": [],
        "working_state": None,
        "interaction": interaction,
        "citation_bundle": None,
    }
