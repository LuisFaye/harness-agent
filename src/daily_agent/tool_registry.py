from __future__ import annotations

import json
from pathlib import Path

from daily_agent.capability_models import ToolDefinition
from daily_agent.database_connections import DatabaseConnectionCatalog, database_tool_names
from daily_agent.artifact_tools import ARTIFACT_SOURCE_ID, ARTIFACT_TOOL_DEFINITIONS
from daily_agent.git_security import GIT_SOURCE_ID, git_runtime_status
from daily_agent.git_tools import GIT_TOOL_DEFINITIONS
from daily_agent.mcp_catalog import McpCatalog, McpServerRecord
from daily_agent.paths import AppPaths
from daily_agent.shell_security import shell_runtime_status
from daily_agent.shell_tools import SHELL_TOOL_DEFINITIONS
from daily_agent.skill_catalog import SkillCatalog, SkillRecord
from daily_agent.workspace_tools import WORKSPACE_TOOL_DEFINITIONS


def skill_tool_id(skill: SkillRecord) -> str:
    tool_name = str(skill.tool_name or skill.name).replace("-", "_")
    return f"skill:{skill.name}:{tool_name}"


def mcp_tool_id(server_id: str, tool_name: str) -> str:
    return f"mcp:{server_id}:{tool_name}"


class ToolRegistry:
    """把 Skill 和 MCP 的真实能力汇总成一份 Tool 视图。"""

    def __init__(
        self,
        *,
        path: Path | None = None,
        paths: AppPaths | None = None,
        skill_catalog: SkillCatalog | None = None,
        mcp_catalog: McpCatalog | None = None,
        database_catalog: DatabaseConnectionCatalog | None = None,
    ) -> None:
        resolved_paths = paths or AppPaths.discover()
        self.path = (path or resolved_paths.capability_registry_path).resolve()
        self.skill_catalog = skill_catalog or SkillCatalog(paths=resolved_paths)
        self.mcp_catalog = mcp_catalog or McpCatalog(paths=resolved_paths)
        self.database_catalog = database_catalog or DatabaseConnectionCatalog(
            paths=resolved_paths,
            path=(path.parent / "database_connections.json") if path is not None else None,
            connection_home=(path.parent / "database_connections") if path is not None else None,
        )
        self.include_database_tools = path is None or database_catalog is not None

    def list_tools(self, *, connected_server_ids: set[str] | None = None) -> list[ToolDefinition]:
        connected = connected_server_ids or set()
        disabled = self._load_disabled()
        items = [self._from_skill(item, disabled) for item in self.skill_catalog.list_skills()]
        tools = [item for item in items if item is not None]
        if self.include_database_tools:
            tools.append(self._database_connect_tool(disabled))
            for connection in self.database_catalog.list_connections():
                tools.extend(self._from_database(connection, disabled, connected))
        tools.extend(self._workspace_tools(disabled))
        tools.extend(self._shell_tools(disabled))
        tools.extend(self._git_tools(disabled))
        tools.extend(self._artifact_tools(disabled))
        for server in self.mcp_catalog.list_servers():
            if server.managed_by == "database_connection":
                continue
            tools.extend(self._from_mcp(server, disabled, connected))
        # workspace 排在 mcp 之后，保持既有「第一个工具」断言语义不变。
        source_order = {
            "skill": 0,
            "database": 1,
            "mcp": 2,
            "workspace": 3,
            "shell": 4,
            "git": 5,
            "artifact": 6,
        }
        return sorted(tools, key=lambda item: (source_order[item.source_type], item.name, item.tool_id))

    def get(self, tool_id: str, *, connected_server_ids: set[str] | None = None) -> ToolDefinition | None:
        return next((item for item in self.list_tools(connected_server_ids=connected_server_ids) if item.tool_id == tool_id), None)

    def set_enabled(self, tool_id: str, enabled: bool) -> bool:
        disabled = self._load_disabled()
        if enabled:
            disabled.discard(tool_id)
        else:
            disabled.add(tool_id)
        self._save_disabled(disabled)
        return enabled

    def is_tool_enabled(self, tool_id: str) -> bool:
        return tool_id not in self._load_disabled()

    def skill_runtime_status(self, skill: SkillRecord) -> dict[str, object]:
        compatibility_status = str((skill.compatibility or {}).get("status") or "native")
        executable = bool(skill.execution) and compatibility_status not in {"unsupported", "error"}
        registered = executable and skill.interaction_mode == "react_tool"
        tool_enabled = self.is_tool_enabled(skill_tool_id(skill)) if registered else False
        callable_by_agent = skill.status == "enabled" and registered and tool_enabled
        reason = None
        if skill.status != "enabled":
            reason = "Skill 已停用。"
        elif not executable:
            reason = skill.error_message or "Skill 没有可执行配置。"
        elif not registered:
            reason = "该 Skill 是命令式或文档型能力，不会注册为 Agent Tool。"
        elif not tool_enabled:
            reason = "对应 Tool 已单独停用。"
        return {
            "imported": skill.source == "imported",
            "enabled": skill.status == "enabled",
            "executable": executable,
            "registered_as_tool": registered,
            "callable_by_agent": callable_by_agent,
            "unavailable_reason": reason,
        }

    def _from_skill(self, skill: SkillRecord, disabled: set[str]) -> ToolDefinition | None:
        status = self.skill_runtime_status(skill)
        if not status["registered_as_tool"]:
            return None
        tool_id = skill_tool_id(skill)
        enabled = skill.status == "enabled" and tool_id not in disabled
        available = bool(status["callable_by_agent"])
        return ToolDefinition(
            tool_id=tool_id,
            name=str(skill.tool_name or skill.name).replace("-", "_"),
            description=skill.description,
            source_type="skill",
            source_id=skill.name,
            input_schema=skill.input_schema or {"type": "object", "properties": {}},
            risk_level=skill.risk_level,
            requires_confirmation=skill.requires_confirmation,
            enabled=enabled,
            available=available,
            unavailable_reason=None if available else str(status["unavailable_reason"] or "当前不可调用。"),
            last_error=skill.error_message,
        )

    def _from_mcp(
        self,
        server: McpServerRecord,
        disabled: set[str],
        connected: set[str],
    ) -> list[ToolDefinition]:
        items = []
        for raw in server.tools:
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            tool_id = mcp_tool_id(server.server_id, name)
            enabled = server.enabled and tool_id not in disabled
            security_blocked = raw.get("security_status") == "blocked"
            available = enabled and server.server_id in connected and not security_blocked
            reason = None
            if not server.enabled:
                reason = "MCP Server 已停用。"
            elif tool_id in disabled:
                reason = "Tool 已单独停用。"
            elif server.server_id not in connected:
                reason = server.last_error or "MCP Server 当前未连接。"
            elif security_blocked:
                findings = [str(item) for item in raw.get("security_findings") or [] if str(item)]
                reason = "MCP Tool 安全审查未通过，已隔离。" + (findings[0] if findings else "")
            items.append(
                ToolDefinition(
                    tool_id=tool_id,
                    name=name,
                    description=str(raw.get("description") or f"来自 {server.name} 的 MCP Tool"),
                    source_type="mcp",
                    source_id=server.server_id,
                    input_schema=raw.get("input_schema") or {"type": "object", "properties": {}},
                    risk_level="medium",
                    requires_confirmation=bool(raw.get("requires_confirmation", False)),
                    enabled=enabled,
                    available=available,
                    unavailable_reason=reason,
                    last_error=server.last_error,
                )
            )
        return items

    def _database_connect_tool(self, disabled: set[str]) -> ToolDefinition:
        tool_id = "database:connection:connect"
        enabled = tool_id not in disabled
        return ToolDefinition(
            tool_id=tool_id,
            name="database_connect",
            description="通过聊天内安全表单保存并测试 MySQL 连接，密码不会发送给模型。",
            source_type="database",
            source_id="connection",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "host": {"type": "string"},
                    "port": {"type": "integer"},
                    "database": {"type": "string"},
                    "username": {"type": "string"},
                },
            },
            risk_level="low",
            requires_confirmation=False,
            enabled=enabled,
            available=enabled,
            unavailable_reason=None if enabled else "Tool 已停用。",
        )

    def _workspace_tools(self, disabled: set[str]) -> list[ToolDefinition]:
        """把五个原生工作区 Tool 注册进能力中心，支持独立启停。"""
        items = []
        for spec in WORKSPACE_TOOL_DEFINITIONS:
            name = str(spec["name"])
            tool_id = f"workspace:workspace-root:{name}"
            enabled = tool_id not in disabled
            requires_confirmation = bool(spec["requires_confirmation"])
            items.append(
                ToolDefinition(
                    tool_id=tool_id,
                    name=name,
                    description=str(spec["description"]),
                    source_type="workspace",
                    source_id="workspace-root",
                    input_schema=spec["parameters"],
                    risk_level="high" if requires_confirmation else "low",
                    requires_confirmation=requires_confirmation,
                    enabled=enabled,
                    available=enabled,
                    unavailable_reason=None if enabled else "Tool 已停用。",
                )
            )
        return items

    def _shell_tools(self, disabled: set[str]) -> list[ToolDefinition]:
        """把三个原生 Shell Tool 注册进能力中心，支持独立启停。

        available 与 shell_runtime_status 保持一致：文件系统隔离后端不可用时，
        三个 Tool 即使启用也标记 available=False，并给出中文 unavailable_reason，
        保证 Agent 与 Prompt Snapshot 不会挂载/宣称不可用能力。
        """
        runtime = shell_runtime_status()
        backend_available = bool(runtime["available"])
        backend_reason = str(runtime.get("unavailable_reason") or "当前平台没有受支持的文件系统隔离后端。")
        items = []
        for spec in SHELL_TOOL_DEFINITIONS:
            name = str(spec["name"])
            tool_id = f"shell:workspace-shell:{name}"
            enabled = tool_id not in disabled
            requires_confirmation = bool(spec["requires_confirmation"])
            available = enabled and backend_available
            reason = None
            if not enabled:
                reason = "Tool 已停用。"
            elif not backend_available:
                reason = backend_reason
            items.append(
                ToolDefinition(
                    tool_id=tool_id,
                    name=name,
                    description=str(spec["description"]),
                    source_type="shell",
                    source_id="workspace-shell",
                    input_schema=spec["parameters"],
                    risk_level="high" if requires_confirmation else "low",
                    requires_confirmation=requires_confirmation,
                    enabled=enabled,
                    available=available,
                    unavailable_reason=reason,
                )
            )
        return items

    def _git_tools(self, disabled: set[str]) -> list[ToolDefinition]:
        """把七个原生 Git Tool 注册进能力中心，支持独立启停。

        available 与 git_runtime_status 保持一致：文件系统隔离后端不可用时，
        七个 Tool 即使启用也标记 available=False，并给出中文 unavailable_reason，
        保证 Agent 与 Prompt Snapshot 不会挂载/宣称不可用能力。
        """
        runtime = git_runtime_status()
        backend_available = bool(runtime["available"])
        backend_reason = str(runtime.get("unavailable_reason") or "当前平台没有受支持的文件系统隔离后端。")
        items = []
        for spec in GIT_TOOL_DEFINITIONS:
            name = str(spec["name"])
            tool_id = f"git:{GIT_SOURCE_ID}:{name}"
            enabled = tool_id not in disabled
            requires_confirmation = bool(spec["requires_confirmation"])
            available = enabled and backend_available
            reason = None
            if not enabled:
                reason = "Tool 已停用。"
            elif not backend_available:
                reason = backend_reason
            items.append(
                ToolDefinition(
                    tool_id=tool_id,
                    name=name,
                    description=str(spec["description"]),
                    source_type="git",
                    source_id=GIT_SOURCE_ID,
                    input_schema=spec["parameters"],
                    risk_level="high" if requires_confirmation else "low",
                    requires_confirmation=requires_confirmation,
                    enabled=enabled,
                    available=available,
                    unavailable_reason=reason,
                )
            )
        return items

    def _artifact_tools(self, disabled: set[str]) -> list[ToolDefinition]:
        """把六个原生 Artifact Tool 注册进能力中心，支持独立启停。"""
        items = []
        for spec in ARTIFACT_TOOL_DEFINITIONS:
            name = str(spec["name"])
            tool_id = f"artifact:{ARTIFACT_SOURCE_ID}:{name}"
            enabled = tool_id not in disabled
            requires_confirmation = bool(spec["requires_confirmation"])
            items.append(
                ToolDefinition(
                    tool_id=tool_id,
                    name=name,
                    description=str(spec["description"]),
                    source_type="artifact",
                    source_id=ARTIFACT_SOURCE_ID,
                    input_schema=spec["parameters"],
                    risk_level="high" if requires_confirmation else "low",
                    requires_confirmation=requires_confirmation,
                    enabled=enabled,
                    available=enabled,
                    unavailable_reason=None if enabled else "Tool 已停用。",
                )
            )
        return items

    def _from_database(self, connection, disabled: set[str], connected: set[str]) -> list[ToolDefinition]:
        available_connection = connection.status == "connected" and connection.mcp_server_id in connected
        items = []
        names = database_tool_names(connection)
        for name in names:
            tool_id = f"database:{connection.connection_id}:{name}"
            enabled = tool_id not in disabled
            is_execute = name.endswith("_execute")
            available = enabled and available_connection
            items.append(
                ToolDefinition(
                    tool_id=tool_id,
                    name=name,
                    description=(
                        f"通过数据库连接「{connection.name}」"
                        + ("执行一条需要确认的写 SQL。" if is_execute else "执行只读数据库操作。")
                    ),
                    source_type="database",
                    source_id=connection.connection_id,
                    input_schema={"type": "object", "properties": {}},
                    risk_level="high" if is_execute else "low",
                    requires_confirmation=is_execute,
                    enabled=enabled,
                    available=available,
                    unavailable_reason=None if available else (
                        "Tool 已停用。" if not enabled else connection.last_error or "数据库当前未连接。"
                    ),
                    last_error=connection.last_error,
                )
            )
        return items

    def _load_disabled(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(item) for item in payload.get("disabled_tool_ids", [])}
        except (OSError, ValueError):
            return set()

    def _save_disabled(self, disabled: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "disabled_tool_ids": sorted(disabled)}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
