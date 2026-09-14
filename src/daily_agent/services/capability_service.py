from __future__ import annotations

from daily_agent.capability_models import (
    McpCapabilityResponse,
    McpPromptRenderResponse,
    McpResourceReadResponse,
    McpServerResponse,
    McpServerWriteRequest,
    ToolDefinition,
    ToolListResponse,
)
from daily_agent.errors import DailyAgentError
from daily_agent.mcp_catalog import McpCatalog, McpServerRecord
from daily_agent.mcp_runtime import McpRuntimeManager, get_mcp_runtime
from daily_agent.tool_registry import ToolRegistry


class ToolService:
    """提供与 Agent 构建规则一致的 Tool 查询和启停。"""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        runtime: McpRuntimeManager | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.runtime = runtime or get_mcp_runtime()

    def list_tools(self) -> ToolListResponse:
        items = self.registry.list_tools(connected_server_ids=self.runtime.connected_server_ids())
        return ToolListResponse(items=items, total=len(items))

    def get_tool(self, tool_id: str) -> ToolDefinition | None:
        return self.registry.get(tool_id, connected_server_ids=self.runtime.connected_server_ids())

    def set_enabled(self, tool_id: str, enabled: bool) -> ToolDefinition | None:
        if self.get_tool(tool_id) is None:
            return None
        self.registry.set_enabled(tool_id, enabled)
        return self.get_tool(tool_id)


class McpService:
    """协调 MCP 配置持久化和当前进程里的真实连接。"""

    def __init__(
        self,
        catalog: McpCatalog | None = None,
        runtime: McpRuntimeManager | None = None,
    ) -> None:
        self.catalog = catalog or McpCatalog()
        self.runtime = runtime or get_mcp_runtime()

    def list_servers(self) -> list[McpServerResponse]:
        return [self._response(item) for item in self.catalog.list_servers()]

    def connect_enabled_servers(self) -> list[McpServerResponse]:
        """应用启动时恢复已启用的 MCP；单个服务失败不能阻塞整个 API 启动。"""
        results: list[McpServerResponse] = []
        for record in self.catalog.list_servers():
            if not record.enabled:
                continue
            try:
                connected = self.connect_server(record.server_id)
            except DailyAgentError:
                connected = self.get_server(record.server_id)
            if connected is not None:
                results.append(connected)
        return results

    def get_server(self, server_id: str) -> McpServerResponse | None:
        record = self.catalog.get(server_id)
        return self._response(record) if record else None

    def create_server(self, request: McpServerWriteRequest) -> McpServerResponse:
        return self._response(self.catalog.create(request))

    def update_server(self, server_id: str, request: McpServerWriteRequest) -> McpServerResponse | None:
        self.runtime.disconnect(server_id)
        record = self.catalog.update(server_id, request)
        return self._response(record) if record else None

    def delete_server(self, server_id: str) -> bool:
        self.runtime.disconnect(server_id)
        return self.catalog.delete(server_id)

    def connect_server(self, server_id: str, *, force: bool = False) -> McpServerResponse | None:
        record = self.catalog.get(server_id)
        if record is None:
            return None
        self.catalog.update_runtime(server_id, status="connecting", last_error=None)
        try:
            capabilities = self.runtime.connect(record, force=force)
            has_approved_capability = any(
                item.get("security_status") != "blocked"
                for kind in ("tools", "resources", "prompts")
                for item in capabilities.get(kind) or []
                if isinstance(item, dict)
            )
            if not has_approved_capability:
                self.runtime.disconnect(server_id)
                raise DailyAgentError(
                    "MCP 已建立连接，但没有发现任何 Tool、Resource 或 Prompt，"
                    "或发现的能力均未通过安全审查。请检查启动命令、参数和服务端能力说明。"
                )
        except DailyAgentError as exc:
            self.catalog.update_runtime(
                server_id,
                status="error",
                last_error=str(exc),
                tools=[],
                resources=[],
                prompts=[],
            )
            raise
        updated = self.catalog.update_runtime(
            server_id,
            status="connected",
            last_error=None,
            tools=capabilities["tools"],
            resources=capabilities["resources"],
            prompts=capabilities["prompts"],
        )
        return self._response(updated) if updated else None

    def disconnect_server(self, server_id: str) -> McpServerResponse | None:
        if self.catalog.get(server_id) is None:
            return None
        self.runtime.disconnect(server_id)
        updated = self.catalog.update_runtime(server_id, status="disconnected", last_error=None)
        return self._response(updated) if updated else None

    def capabilities(self, server_id: str) -> McpCapabilityResponse | None:
        server = self.get_server(server_id)
        if server is None:
            return None
        return McpCapabilityResponse(
            server_id=server.server_id,
            status=server.status,
            tools=server.tools,
            resources=server.resources,
            prompts=server.prompts,
        )

    def read_resource(self, server_id: str, uri: str) -> McpResourceReadResponse | None:
        if self.catalog.get(server_id) is None:
            return None
        contents = self.runtime.read_resource(server_id, uri)
        return McpResourceReadResponse(server_id=server_id, uri=uri, contents=contents)

    def render_prompt(
        self,
        server_id: str,
        name: str,
        arguments: dict[str, str],
    ) -> McpPromptRenderResponse | None:
        if self.catalog.get(server_id) is None:
            return None
        result = self.runtime.render_prompt(server_id, name, arguments)
        return McpPromptRenderResponse(
            server_id=server_id,
            name=name,
            description=result.get("description"),
            messages=result.get("messages") or [],
        )

    def _response(self, record: McpServerRecord) -> McpServerResponse:
        return McpServerResponse.model_validate(
            record.to_dict(runtime_connected=self.runtime.is_connected(record.server_id))
        )
