from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from daily_agent.capability_models import McpServerWriteRequest
from daily_agent.errors import DailyAgentError
from daily_agent.paths import AppPaths


@dataclass
class McpServerRecord:
    server_id: str
    name: str
    transport_type: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    environment_keys: list[str] = field(default_factory=list)
    header_env_map: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    status: str = "disconnected"
    last_connected_at: str | None = None
    last_error: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    managed_by: str | None = None
    connection_id: str | None = None
    hidden: bool = False

    def to_dict(self, *, runtime_connected: bool | None = None) -> dict[str, Any]:
        status = self.status
        if runtime_connected is False and status == "connected":
            status = "disconnected"
        return {
            "server_id": self.server_id,
            "name": self.name,
            "transport_type": self.transport_type,
            "command": self.command,
            "args": list(self.args),
            "url": self.url,
            "environment_keys": list(self.environment_keys),
            "header_env_map": dict(self.header_env_map),
            "enabled": self.enabled,
            "status": status,
            "last_connected_at": self.last_connected_at,
            "last_error": self.last_error,
            "tools": list(self.tools),
            "resources": list(self.resources),
            "prompts": list(self.prompts),
            "managed_by": self.managed_by,
            "connection_id": self.connection_id,
            "hidden": self.hidden,
        }


class McpCatalog:
    def __init__(self, path: Path | None = None, *, paths: AppPaths | None = None) -> None:
        self.path = (path or (paths or AppPaths.discover()).mcp_registry_path).resolve()

    def list_servers(self) -> list[McpServerRecord]:
        payload = self._load()
        return sorted(
            [self._from_dict(item) for item in payload.get("servers", []) if isinstance(item, dict)],
            key=lambda item: item.name.lower(),
        )

    def get(self, server_id: str) -> McpServerRecord | None:
        return next((item for item in self.list_servers() if item.server_id == server_id), None)

    def create(self, request: McpServerWriteRequest) -> McpServerRecord:
        items = self.list_servers()
        if any(item.name == request.name.strip() for item in items):
            raise DailyAgentError("MCP Server 名称已存在。")
        slug = re.sub(r"[^a-z0-9]+", "-", request.name.lower()).strip("-") or "server"
        record = self._from_request(f"mcp-{slug}-{uuid.uuid4().hex[:8]}", request)
        items.append(record)
        self._save(items)
        return record

    def update(self, server_id: str, request: McpServerWriteRequest) -> McpServerRecord | None:
        items = self.list_servers()
        current = next((item for item in items if item.server_id == server_id), None)
        if current is None:
            return None
        if any(item.server_id != server_id and item.name == request.name.strip() for item in items):
            raise DailyAgentError("MCP Server 名称已存在。")
        replacement = self._from_request(server_id, request)
        replacement.tools = current.tools
        replacement.resources = current.resources
        replacement.prompts = current.prompts
        self._save([replacement if item.server_id == server_id else item for item in items])
        return replacement

    def delete(self, server_id: str) -> bool:
        items = self.list_servers()
        remaining = [item for item in items if item.server_id != server_id]
        if len(remaining) == len(items):
            return False
        self._save(remaining)
        return True

    def mark_database_managed(self, server_id: str, connection_id: str) -> McpServerRecord | None:
        """把 MCP 标记成数据库连接的内部适配器，避免前端和 Agent 暴露两套入口。"""
        items = self.list_servers()
        current = next((item for item in items if item.server_id == server_id), None)
        if current is None:
            return None
        current.managed_by = "database_connection"
        current.connection_id = connection_id
        current.hidden = True
        self._save(items)
        return current

    def update_runtime(
        self,
        server_id: str,
        *,
        status: str,
        last_error: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        resources: list[dict[str, Any]] | None = None,
        prompts: list[dict[str, Any]] | None = None,
    ) -> McpServerRecord | None:
        items = self.list_servers()
        current = next((item for item in items if item.server_id == server_id), None)
        if current is None:
            return None
        current.status = status
        current.last_error = last_error
        if status == "connected":
            current.last_connected_at = datetime.now(timezone.utc).isoformat()
        if tools is not None:
            current.tools = tools
        if resources is not None:
            current.resources = resources
        if prompts is not None:
            current.prompts = prompts
        self._save(items)
        return current

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "servers": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"version": 1, "servers": []}
        except (OSError, ValueError) as exc:
            raise DailyAgentError(f"MCP 配置文件损坏：{exc}") from exc

    def _save(self, items: list[McpServerRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "servers": [item.to_dict() for item in items]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _from_request(server_id: str, request: McpServerWriteRequest) -> McpServerRecord:
        return McpServerRecord(
            server_id=server_id,
            name=request.name.strip(),
            transport_type=request.transport_type,
            command=str(request.command or "").strip() or None,
            args=_normalize_stdio_args(request.args),
            url=str(request.url or "").strip() or None,
            environment_keys=sorted({str(item).strip() for item in request.environment_keys if str(item).strip()}),
            header_env_map={str(key).strip(): str(value).strip() for key, value in request.header_env_map.items()},
            enabled=request.enabled,
        )

    @staticmethod
    def _from_dict(payload: dict[str, Any]) -> McpServerRecord:
        return McpServerRecord(
            server_id=str(payload.get("server_id") or ""),
            name=str(payload.get("name") or ""),
            transport_type=str(payload.get("transport_type") or "stdio"),
            command=payload.get("command"),
            args=_normalize_stdio_args(payload.get("args") or []),
            url=payload.get("url"),
            environment_keys=[str(item) for item in payload.get("environment_keys") or []],
            header_env_map={str(key): str(value) for key, value in (payload.get("header_env_map") or {}).items()},
            enabled=bool(payload.get("enabled", True)),
            status=str(payload.get("status") or "disconnected"),
            last_connected_at=payload.get("last_connected_at"),
            last_error=payload.get("last_error"),
            tools=list(payload.get("tools") or []),
            resources=list(payload.get("resources") or []),
            prompts=list(payload.get("prompts") or []),
            managed_by=str(payload.get("managed_by") or "") or None,
            connection_id=str(payload.get("connection_id") or "") or None,
            hidden=bool(payload.get("hidden", False)),
        )


def _normalize_stdio_args(raw_args: list[Any]) -> list[str]:
    """兼容误把 JSON 数组逐行粘贴到手动参数框的历史配置。"""
    args = [str(item).strip() for item in raw_args if str(item).strip()]
    if not args:
        return []

    # 只有每一行都符合 JSON 字符串元素的外观时才尝试修复，避免改写正常参数中的引号或逗号。
    json_fragments = [item[:-1].rstrip() if item.endswith(",") else item for item in args]
    if not all(len(item) >= 2 and item.startswith('"') and item.endswith('"') for item in json_fragments):
        return args
    try:
        decoded = json.loads(f"[{','.join(json_fragments)}]")
    except (TypeError, ValueError):
        return args
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        return args
    return decoded
