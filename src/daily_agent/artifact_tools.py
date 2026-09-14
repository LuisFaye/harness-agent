"""六个原生 Artifact Tool：把 Agent 生成的结果登记为可追溯、可下载的产物。

能力清单
--------
1. artifact_create_text：创建 md/txt/csv/json/html/svg 文本产物（需确认）；
2. artifact_register_file：把安全工作区文件复制为不可变快照（需确认）；
3. artifact_list：列出当前 user + session + run 的产物（无需确认）；
4. artifact_get：按 ID 读取当前 user + session + run 的公开元数据（无需确认）；
5. artifact_set_retained：切换保留状态（需确认）；
6. artifact_delete：删除当前 user + session + run 的产物（需确认）。

所有 entrypoint 通过 ``build_artifact_tools`` 闭包绑定本轮 user/session/run，
包括 set_retained/delete：跨 session/run 的 Tool 访问一律返回「产物不存在」；
run_id 为空时生成仅当前 Agent 实例使用的稳定 owner id，不让不同默认运行共享。
四个有副作用 Tool 复用现有确认机制：确认后同名 Tool 不再重复确认。
Tool 返回精简 JSON：只含公开记录或删除结果，不包含 Artifact 正文与任何绝对路径。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from daily_agent.artifact_service import (
    ArtifactNotFoundError,
    ArtifactService,
    ArtifactValidationError,
)
from daily_agent.user_workspace import user_workspace_root


# ToolRegistry 与 Agent 构建共用。
ARTIFACT_SOURCE_TYPE = "artifact"
ARTIFACT_SOURCE_ID = "artifact-center"


# 六个 Artifact Tool 的稳定定义，ToolRegistry 与 Agent 构建共用。
ARTIFACT_TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "artifact_create_text",
        "description": (
            "把本次回答或整理结果创建为持久化 Artifact 文本产物，支持 Markdown(.md)、"
            "纯文本(.txt)、CSV(.csv) 和 JSON(.json)；最大 2MB，空内容拒绝，JSON 必须可解析。"
            "创建后可通过 artifact_list / artifact_get 查看公开元数据并下载；"
            "执行前需要用户确认。返回公开元数据，不包含正文与绝对路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "产物文件名，扩展名必须是 .md/.txt/.csv/.json 之一"},
                "content": {"type": "string", "description": "产物完整文本内容"},
                "summary": {"type": "string", "description": "可选单行摘要，最多 500 字符"},
                "retained": {
                    "type": "boolean",
                    "description": "是否永久保留；true 时不过期，默认 false（30 天后清理）",
                },
            },
            "required": ["filename", "content"],
            "additionalProperties": False,
        },
        "requires_confirmation": True,
    },
    {
        "name": "artifact_register_file",
        "description": (
            "把工作区内的真实文件复制为不可变 Artifact 快照并登记，支持 "
            ".md/.txt/.csv/.json 以及 .docx/.pdf/.xlsx/.png/.jpg/.jpeg/.webp；"
            "最大 10MB。用户要下载或预览的文件请放到 outputs/ 再登记。"
            "登记后源文件变化不影响产物内容。拒绝符号链接、目录、越界、敏感路径、空文件。"
            "执行前需要用户确认。返回公开元数据，不包含正文与绝对路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "工作区相对路径或仍位于工作区内的绝对路径，用户交付物请用 outputs/文件名",
                },
                "filename": {
                    "type": "string",
                    "description": "可选产物文件名；缺省用源文件 basename，扩展名必须受支持",
                },
                "summary": {"type": "string", "description": "可选单行摘要，最多 500 字符"},
                "retained": {
                    "type": "boolean",
                    "description": "是否永久保留；true 时不过期，默认 false（30 天后清理）",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "requires_confirmation": True,
    },
    {
        "name": "artifact_list",
        "description": (
            "列出当前用户、当前会话、当前运行下的 Artifact 公开元数据，按创建时间倒序；"
            "只读，无需确认。返回精简 JSON，不包含正文与绝对路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "最多返回条数，1 到 100，默认 20",
                },
                "offset": {
                    "type": "integer",
                    "description": "跳过条数，默认 0",
                },
            },
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "artifact_get",
        "description": (
            "按 artifact_id 读取当前用户、当前会话、当前运行下的 Artifact 公开元数据；"
            "只读，无需确认。不返回正文内容，也不返回任何绝对路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "32 位小写十六进制产物 ID"},
            },
            "required": ["artifact_id"],
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "artifact_set_retained",
        "description": (
            "切换一条 Artifact 的保留状态：retained=true 永久保留不过期，"
            "retained=false 从当前时间重新计算 30 天后过期；执行前需要用户确认。"
            "返回更新后的公开元数据。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "32 位小写十六进制产物 ID"},
                "retained": {"type": "boolean", "description": "true=永久保留；false=重新计算 30 天过期"},
            },
            "required": ["artifact_id", "retained"],
            "additionalProperties": False,
        },
        "requires_confirmation": True,
    },
    {
        "name": "artifact_delete",
        "description": (
            "按 artifact_id 硬删除当前用户、当前会话、当前运行下的一条 Artifact，"
            "同时删除内容文件与元数据；执行前需要用户确认。返回删除结果，不返回正文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "32 位小写十六进制产物 ID"},
            },
            "required": ["artifact_id"],
            "additionalProperties": False,
        },
        "requires_confirmation": True,
    },
)


def build_artifact_tools(
    user_id: str,
    session_id: str,
    run_id: str | None,
    confirmed_tool_names: set[str] | None = None,
) -> list[Any]:
    """构建闭包绑定本轮归属的 Agno Artifact Tool 列表。

    run_id 为空时生成仅当前 Agent 实例使用的稳定 owner id：每次构建各得一个
    随机 id，不同默认运行（不同 Agent 实例）之间不会共享产物记录。
    """
    from agno.tools.function import Function
    from daily_agent.tool_registry import ToolRegistry

    service = ArtifactService(workspace_root=user_workspace_root(user_id))
    owner_run_id = str(run_id or "").strip() or f"local-{uuid.uuid4().hex}"
    confirmed = {str(item).replace("-", "_") for item in (confirmed_tool_names or set())}
    registry = ToolRegistry()

    def _error(exc: Exception) -> str:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    def create_text(filename: str = "", content: str = "", summary: str | None = None, retained: bool = False):
        try:
            record = service.create_text(
                filename=filename,
                content=content,
                summary=summary,
                retained=_as_bool(retained),
                user_id=user_id,
                session_id=session_id,
                run_id=owner_run_id,
            )
            return json.dumps(record.to_public_dict(), ensure_ascii=False)
        except (ArtifactValidationError, ArtifactNotFoundError) as exc:
            return _error(exc)

    def register_file(path: str = "", filename: str | None = None, summary: str | None = None, retained: bool = False):
        try:
            record = service.register_file(
                path=path,
                filename=filename,
                summary=summary,
                retained=_as_bool(retained),
                user_id=user_id,
                session_id=session_id,
                run_id=owner_run_id,
            )
            return json.dumps(record.to_public_dict(), ensure_ascii=False)
        except (ArtifactValidationError, ArtifactNotFoundError) as exc:
            return _error(exc)

    def artifact_list(limit: int = 20, offset: int = 0):
        try:
            # 非整数 limit/offset 统一转成精简错误 JSON，不让 ValueError 逃逸。
            try:
                page_limit = max(1, min(100, int(limit)))
                page_offset = max(0, int(offset))
            except (TypeError, ValueError) as exc:
                raise ArtifactValidationError("limit 和 offset 必须是整数。") from exc
            records, total = service.list_records(
                user_id=user_id,
                session_id=session_id,
                run_id=owner_run_id,
                limit=page_limit,
                offset=page_offset,
            )
            return json.dumps(
                {
                    "items": [record.to_public_dict() for record in records],
                    "total": total,
                    "limit": page_limit,
                    "offset": page_offset,
                },
                ensure_ascii=False,
            )
        except (ArtifactValidationError, ArtifactNotFoundError) as exc:
            return _error(exc)

    def artifact_get(artifact_id: str = ""):
        try:
            record = service.get(
                artifact_id,
                user_id=user_id,
                session_id=session_id,
                run_id=owner_run_id,
            )
            return json.dumps(record.to_public_dict(), ensure_ascii=False)
        except (ArtifactValidationError, ArtifactNotFoundError) as exc:
            return _error(exc)

    def artifact_set_retained(artifact_id: str = "", retained: bool = False):
        try:
            record = service.set_retained(
                artifact_id,
                retained=_as_bool(retained),
                user_id=user_id,
                session_id=session_id,
                run_id=owner_run_id,
            )
            return json.dumps(record.to_public_dict(), ensure_ascii=False)
        except (ArtifactValidationError, ArtifactNotFoundError) as exc:
            return _error(exc)

    def artifact_delete(artifact_id: str = ""):
        try:
            result = service.delete(
                artifact_id,
                user_id=user_id,
                session_id=session_id,
                run_id=owner_run_id,
            )
            return json.dumps(result, ensure_ascii=False)
        except (ArtifactValidationError, ArtifactNotFoundError) as exc:
            return _error(exc)

    entrypoints = {
        "artifact_create_text": create_text,
        "artifact_register_file": register_file,
        "artifact_list": artifact_list,
        "artifact_get": artifact_get,
        "artifact_set_retained": artifact_set_retained,
        "artifact_delete": artifact_delete,
    }
    tools: list[Any] = []
    for spec in ARTIFACT_TOOL_DEFINITIONS:
        name = str(spec["name"])
        tool_id = f"artifact:{ARTIFACT_SOURCE_ID}:{name}"
        if not registry.is_tool_enabled(tool_id):
            continue
        needs_confirmation = bool(spec["requires_confirmation"]) and name not in confirmed
        tools.append(
            Function(
                name=name,
                description=str(spec["description"]),
                parameters=spec["parameters"],
                entrypoint=entrypoints[name],
                skip_entrypoint_processing=True,
                requires_confirmation=needs_confirmation,
            )
        )
    return tools


def _as_bool(value: Any) -> bool:
    """把模型可能传来的字符串布尔安全转成 bool；无法识别时报错。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value or "").strip().lower()
    if lowered in {"true", "1", "yes", "是"}:
        return True
    if lowered in {"false", "0", "no", "否"}:
        return False
    raise ArtifactValidationError("retained 必须是 true 或 false。")
