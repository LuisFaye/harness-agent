"""截获发给 OpenAI 兼容模型的完整请求体，供运行监控详情展示。

设计约定（与产品确认一致）：
1. 只在 Agent 主循环期间开启收集器；
2. 完整请求写入本地 ``data_dir/model-requests/`` 短命文件，不进 MySQL；
3. 仅 owner 可见；读取时再走现有脱敏与长度限制；
4. 按「单轮 + 整次」字符预算压缩，避免单文件过大。

实现注意：
Agent 实际模型调用发生在 ``agent_event_stream`` 的独立线程里，不能只用
ContextVar（跨线程默认不继承）。这里用进程内栈 + 锁，主线程开启、工作线程写入。
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# 单次运行最多保留多少轮请求；超出后丢弃最早的轮次并打标。
_MAX_TURNS = 16
# 单轮序列化后的大致字符上限（含 messages / tools）。
_MAX_CHARS_PER_TURN = 80_000
# 整次数组序列化后的大致字符上限。
_MAX_CHARS_TOTAL = 240_000

_lock = threading.RLock()
# 嵌套开启时后进先出；同一时刻通常只有一层 Agent 主循环。
_active_buckets: list[list[dict[str, Any]]] = []


@contextmanager
def capturing_model_requests() -> Iterator[None]:
    """在 Agent 主循环内开启收集；工作线程写入同一份 list。"""
    bucket: list[dict[str, Any]] = []
    with _lock:
        _active_buckets.append(bucket)
    try:
        yield
    finally:
        with _lock:
            if bucket in _active_buckets:
                _active_buckets.remove(bucket)


def is_capturing_model_requests() -> bool:
    """当前是否存在开启中的模型请求截获窗口。"""
    with _lock:
        return bool(_active_buckets)


def record_openai_compatible_request(request_body: dict[str, Any]) -> None:
    """把一轮即将发出的 chat.completions 请求记入当前收集器。

    未开启截获时直接返回，避免标题生成等旁路调用污染主运行记录。
    任何序列化异常只记日志，绝不阻断真实模型调用。
    """
    with _lock:
        if not _active_buckets:
            return
        bucket = _active_buckets[-1]
    try:
        safe_body = _json_safe(request_body)
        messages = safe_body.get("messages") if isinstance(safe_body.get("messages"), list) else []
        tools = safe_body.get("tools") if isinstance(safe_body.get("tools"), list) else []
        with _lock:
            record: dict[str, Any] = {
                "turn": len(bucket) + 1,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "model": safe_body.get("model"),
                "stream": bool(safe_body.get("stream")),
                "message_count": len(messages),
                "tool_count": len(tools),
                "roles": [
                    str(item.get("role") or "unknown")
                    for item in messages
                    if isinstance(item, dict)
                ],
                "request": safe_body,
                "truncated": False,
            }
            record = _enforce_per_turn_budget(record)
            bucket.append(record)
            # 超过轮次上限时丢掉最早的，并给下一次 snapshot 留出标记空间。
            while len(bucket) > _MAX_TURNS:
                bucket.pop(0)
                for index, item in enumerate(bucket, start=1):
                    item["turn"] = index
                bucket[0]["dropped_earlier_turns"] = True
    except Exception:
        logger.exception("截获模型请求失败，已忽略本轮记录")


def snapshot_captured_model_requests() -> list[dict[str, Any]]:
    """读取当前窗口内已截获的请求，并按整次预算再压缩一份可落库副本。"""
    with _lock:
        if not _active_buckets:
            return []
        bucket = list(_active_buckets[-1])
    if not bucket:
        return []
    return _enforce_total_budget(copy.deepcopy(bucket))


def _enforce_per_turn_budget(record: dict[str, Any]) -> dict[str, Any]:
    """单轮超限时优先截断 messages 正文，再必要时丢掉 tools 细节。"""
    encoded = _dumps(record)
    if len(encoded) <= _MAX_CHARS_PER_TURN:
        return record
    request = record.get("request")
    if not isinstance(request, dict):
        record["truncated"] = True
        record["request"] = {"truncated": True, "reason": "request_too_large"}
        return record

    messages = request.get("messages")
    if isinstance(messages, list):
        request["messages"] = [_truncate_message(item) for item in messages]
    encoded = _dumps(record)
    if len(encoded) <= _MAX_CHARS_PER_TURN:
        record["truncated"] = True
        return record

    tools = request.get("tools")
    if isinstance(tools, list) and tools:
        # tools schema 往往占大头：保留名称列表，去掉完整 parameters。
        request["tools"] = [
            {
                "type": item.get("type") if isinstance(item, dict) else "function",
                "function": {
                    "name": (
                        ((item.get("function") or {}).get("name"))
                        if isinstance(item, dict)
                        else None
                    ),
                    "description": "[已截断：工具 schema 过长]",
                },
            }
            for item in tools
            if isinstance(item, dict)
        ]
        request["tools_schema_omitted"] = True
    encoded = _dumps(record)
    if len(encoded) <= _MAX_CHARS_PER_TURN:
        record["truncated"] = True
        return record

    # 仍超限：只保留摘要，不再塞完整 request。
    record["truncated"] = True
    record["request"] = {
        "model": request.get("model"),
        "stream": request.get("stream"),
        "message_count": record.get("message_count"),
        "tool_count": record.get("tool_count"),
        "roles": record.get("roles"),
        "omitted": True,
        "reason": "exceeded_per_turn_budget",
    }
    return record


def _enforce_total_budget(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整次数组超限时从最早轮次开始降级为摘要。"""
    if not records:
        return []
    if len(_dumps(records)) <= _MAX_CHARS_TOTAL:
        return records
    compacted = list(records)
    for index in range(len(compacted)):
        if len(_dumps(compacted)) <= _MAX_CHARS_TOTAL:
            break
        item = compacted[index]
        request = item.get("request") if isinstance(item.get("request"), dict) else {}
        compacted[index] = {
            "turn": item.get("turn"),
            "captured_at": item.get("captured_at"),
            "model": item.get("model"),
            "stream": item.get("stream"),
            "message_count": item.get("message_count"),
            "tool_count": item.get("tool_count"),
            "roles": item.get("roles"),
            "truncated": True,
            "request": {
                "model": request.get("model") or item.get("model"),
                "omitted": True,
                "reason": "exceeded_total_budget",
                "message_count": item.get("message_count"),
                "tool_count": item.get("tool_count"),
                "roles": item.get("roles"),
            },
        }
    return compacted


def _truncate_message(message: Any) -> Any:
    """截断单条 message 的 content / 工具结果，保留 role 与结构。"""
    if not isinstance(message, dict):
        return message
    item = dict(message)
    content = item.get("content")
    if isinstance(content, str) and len(content) > 4000:
        item["content"] = content[:4000] + "\n...[内容过长，已截断]"
        item["content_truncated"] = True
    elif isinstance(content, list):
        # 多模态 parts：只保留文本预览，避免图片 base64 撑爆。
        preview_parts: list[Any] = []
        for part in content[:12]:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type == "text":
                text = str(part.get("text") or "")
                if len(text) > 2000:
                    text = text[:2000] + "…[已截断]"
                preview_parts.append({"type": "text", "text": text})
            elif part_type in {"image_url", "image"}:
                preview_parts.append({"type": part_type, "omitted": True})
            else:
                preview_parts.append({"type": part_type or "unknown", "omitted": True})
        item["content"] = preview_parts
        item["content_truncated"] = True
    tool_calls = item.get("tool_calls")
    if isinstance(tool_calls, list):
        slim_calls = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            arguments = str(function.get("arguments") or "")
            if len(arguments) > 4000:
                arguments = arguments[:4000] + "…[已截断]"
            slim_calls.append(
                {
                    "id": call.get("id"),
                    "type": call.get("type") or "function",
                    "function": {
                        "name": function.get("name"),
                        "arguments": arguments,
                    },
                }
            )
        item["tool_calls"] = slim_calls
    return item


def _json_safe(value: Any) -> Any:
    """递归转成可 JSON 序列化的结构，失败时降级为字符串。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _json_safe(value.dict())
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
