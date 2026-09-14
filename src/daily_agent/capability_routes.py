from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query

from daily_agent.auth_dependencies import require_owner, require_user
from daily_agent.auth_models import AuthenticatedUser
from daily_agent.capability_models import (
    McpCapabilityResponse,
    McpPromptRenderRequest,
    McpPromptRenderResponse,
    McpResourceReadResponse,
    McpServerResponse,
    McpServerWriteRequest,
    ToolDefinition,
    ToolListResponse,
)
from daily_agent.database_models import (
    DatabaseConnectionListResponse,
    DatabaseConnectionResponse,
    DatabaseConnectionSaveResponse,
    DatabaseConnectionWriteRequest,
)


def register_capability_routes(app: FastAPI, *, tool_service, mcp_service, database_service) -> None:
    """集中注册 Tool、数据库连接和高级 MCP 接口，避免主 API 入口继续膨胀。"""

    @app.get(
        "/tools",
        response_model=ToolListResponse,
        summary="查看 Agent 真实可注册的工具",
        description="合并 Skill Tool、数据库 Tool 和 MCP Tool，并返回当前是否真的可调用。",
    )
    def list_tools(_: AuthenticatedUser = Depends(require_user)):
        return tool_service.list_tools()

    @app.get("/tools/{tool_id}", response_model=ToolDefinition, summary="查看一个工具的真实状态")
    def get_tool(tool_id: str, _: AuthenticatedUser = Depends(require_user)):
        item = tool_service.get_tool(tool_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"code": "tool_not_found", "message": "没有找到这个 Tool。"})
        return item

    @app.post("/tools/{tool_id}/enable", response_model=ToolDefinition, summary="启用一个工具")
    def enable_tool(tool_id: str, _: AuthenticatedUser = Depends(require_owner)):
        item = tool_service.set_enabled(tool_id, True)
        if item is None:
            raise HTTPException(status_code=404, detail={"code": "tool_not_found", "message": "没有找到这个 Tool。"})
        return item

    @app.post("/tools/{tool_id}/disable", response_model=ToolDefinition, summary="停用一个工具")
    def disable_tool(tool_id: str, _: AuthenticatedUser = Depends(require_owner)):
        item = tool_service.set_enabled(tool_id, False)
        if item is None:
            raise HTTPException(status_code=404, detail={"code": "tool_not_found", "message": "没有找到这个 Tool。"})
        return item

    @app.get(
        "/database/connections",
        response_model=DatabaseConnectionListResponse,
        summary="查看 Agent 已保存的 MySQL 连接",
        description="返回连接别名和运行状态，不返回密码、envfile 路径或完整连接串。",
    )
    def list_database_connections(_: AuthenticatedUser = Depends(require_owner)):
        items = database_service.list_connections()
        return DatabaseConnectionListResponse(items=items, total=len(items))

    @app.post(
        "/database/connections",
        response_model=DatabaseConnectionSaveResponse,
        summary="保存并测试 MySQL 连接",
        description=(
            "密码只写入本机权限为 0600 的私密 env 文件，不进入模型上下文、聊天记录或事件流。"
            "携带 interaction_request_id 时会返回不含密钥的原任务供前端继续执行。"
        ),
    )
    def create_database_connection(
        request: DatabaseConnectionWriteRequest,
        _: AuthenticatedUser = Depends(require_owner),
    ):
        return database_service.create(request)

    @app.put(
        "/database/connections/{connection_id}",
        response_model=DatabaseConnectionSaveResponse,
        summary="更新并重新测试 MySQL 连接",
        description="password 留空时保留原密码；外部 envfile 纳管的连接不能通过本接口改写。",
    )
    def update_database_connection(
        connection_id: str,
        request: DatabaseConnectionWriteRequest,
        _: AuthenticatedUser = Depends(require_owner),
    ):
        item = database_service.update(connection_id, request)
        if item is None:
            raise _database_not_found()
        return item

    @app.post(
        "/database/connections/{connection_id}/test",
        response_model=DatabaseConnectionResponse,
        summary="连接 MySQL 并执行只读健康检查",
        description="执行 SELECT DATABASE()、CURRENT_USER() 和 SELECT 1，不执行任何写操作。",
    )
    def test_database_connection(connection_id: str, _: AuthenticatedUser = Depends(require_owner)):
        item = database_service.test(connection_id)
        if item is None:
            raise _database_not_found()
        return item

    @app.delete("/database/connections/{connection_id}", summary="删除数据库连接")
    def delete_database_connection(connection_id: str, _: AuthenticatedUser = Depends(require_owner)):
        if not database_service.delete(connection_id):
            raise _database_not_found()
        return {"status": "success", "deleted": True, "connection_id": connection_id}

    @app.get("/mcp/servers", response_model=list[McpServerResponse], summary="查看高级 MCP Server 列表")
    def list_mcp_servers(_: AuthenticatedUser = Depends(require_owner)):
        catalog = getattr(mcp_service, "catalog", None)
        return [
            item
            for item in mcp_service.list_servers()
            if catalog is None or not getattr(catalog.get(item.server_id), "hidden", False)
        ]

    @app.post("/mcp/servers", response_model=McpServerResponse, summary="新增 MCP Server")
    def create_mcp_server(request: McpServerWriteRequest, _: AuthenticatedUser = Depends(require_owner)):
        return mcp_service.create_server(request)

    @app.get("/mcp/servers/{server_id}", response_model=McpServerResponse, summary="查看 MCP Server 详情")
    def get_mcp_server(server_id: str, _: AuthenticatedUser = Depends(require_owner)):
        return _mcp_item_or_404(mcp_service.get_server(server_id))

    @app.put("/mcp/servers/{server_id}", response_model=McpServerResponse, summary="更新 MCP Server")
    def update_mcp_server(
        server_id: str,
        request: McpServerWriteRequest,
        _: AuthenticatedUser = Depends(require_owner),
    ):
        return _mcp_item_or_404(mcp_service.update_server(server_id, request))

    @app.delete("/mcp/servers/{server_id}", summary="删除 MCP Server")
    def delete_mcp_server(server_id: str, _: AuthenticatedUser = Depends(require_owner)):
        if not mcp_service.delete_server(server_id):
            raise _mcp_not_found()
        return {"status": "success", "deleted": True, "server_id": server_id}

    @app.post("/mcp/servers/{server_id}/connect", response_model=McpServerResponse, summary="连接并检查 MCP Server")
    def connect_mcp_server(server_id: str, _: AuthenticatedUser = Depends(require_owner)):
        return _mcp_item_or_404(mcp_service.connect_server(server_id))

    @app.post("/mcp/servers/{server_id}/disconnect", response_model=McpServerResponse, summary="断开 MCP Server")
    def disconnect_mcp_server(server_id: str, _: AuthenticatedUser = Depends(require_owner)):
        return _mcp_item_or_404(mcp_service.disconnect_server(server_id))

    @app.post("/mcp/servers/{server_id}/sync", response_model=McpServerResponse, summary="重新连接并同步 MCP 能力")
    def sync_mcp_server(server_id: str, _: AuthenticatedUser = Depends(require_owner)):
        return _mcp_item_or_404(mcp_service.connect_server(server_id, force=True))

    @app.get(
        "/mcp/servers/{server_id}/capabilities",
        response_model=McpCapabilityResponse,
        summary="查看 MCP 同步出的 Tool、Resource 和 Prompt",
    )
    def get_mcp_capabilities(server_id: str, _: AuthenticatedUser = Depends(require_owner)):
        return _mcp_item_or_404(mcp_service.capabilities(server_id))

    @app.get(
        "/mcp/servers/{server_id}/resources/read",
        response_model=McpResourceReadResponse,
        summary="读取 MCP Resource",
    )
    def read_mcp_resource(
        server_id: str,
        uri: str = Query(description="Resource 的完整 URI"),
        _: AuthenticatedUser = Depends(require_owner),
    ):
        return _mcp_item_or_404(mcp_service.read_resource(server_id, uri))

    @app.post(
        "/mcp/servers/{server_id}/prompts/{prompt_name}/render",
        response_model=McpPromptRenderResponse,
        summary="渲染 MCP Prompt",
    )
    def render_mcp_prompt(
        server_id: str,
        prompt_name: str,
        request: McpPromptRenderRequest,
        _: AuthenticatedUser = Depends(require_owner),
    ):
        return _mcp_item_or_404(mcp_service.render_prompt(server_id, prompt_name, request.arguments))


def _database_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "database_connection_not_found", "message": "没有找到这个数据库连接。"},
    )


def _mcp_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "mcp_server_not_found", "message": "没有找到这个 MCP Server。"},
    )


def _mcp_item_or_404(item):
    if item is None:
        raise _mcp_not_found()
    return item
