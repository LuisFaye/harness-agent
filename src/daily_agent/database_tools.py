from __future__ import annotations

import json
import re
from typing import Any

from daily_agent.database_connections import (
    DatabaseConnectionCatalog,
    DatabaseInteractionStore,
    classify_mysql_sql,
    database_tool_names,
)
from daily_agent.errors import DailyAgentError
from daily_agent.mcp_catalog import McpCatalog
from daily_agent.mcp_runtime import McpRuntimeManager, get_mcp_runtime

_TABLE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+)?$")


def build_database_tools(
    *,
    user_id: str,
    session_id: str,
    query: str,
    confirmed_tool_names: set[str] | None = None,
) -> list[Any]:
    """构建 Agent 原生数据库 Tool，底层 MCP 名称和密码都不会暴露给模型。"""
    from agno.tools.function import Function
    from daily_agent.tool_registry import ToolRegistry

    interaction_store = DatabaseInteractionStore()
    tool_registry = ToolRegistry()

    def request_connection(
        name: str = "",
        host: str = "",
        port: int = 3306,
        database: str = "",
        username: str = "",
    ) -> str:
        interaction = interaction_store.create(
            user_id=user_id,
            session_id=session_id,
            query=query,
            fields={
                "name": name,
                "host": host,
                "port": port,
                "database": database,
                "username": username,
            },
        )
        return json.dumps(interaction.model_dump(), ensure_ascii=False)

    tools: list[Any] = []
    if tool_registry.is_tool_enabled("database:connection:connect"):
        tools.append(Function(
            name="database_connect",
            description=(
                "请求用户安全地连接一个 MySQL 数据库。"
                "只传连接别名、主机、端口、数据库名和用户名，绝不能索要、猜测或传递密码。"
                "当用户要求连接数据库但当前没有合适连接时调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "连接别名"},
                    "host": {"type": "string", "description": "MySQL 主机，可留空让用户填写"},
                    "port": {"type": "integer", "description": "MySQL 端口，默认 3306"},
                    "database": {"type": "string", "description": "默认数据库名"},
                    "username": {"type": "string", "description": "登录用户名"},
                },
                "additionalProperties": False,
            },
            entrypoint=request_connection,
            skip_entrypoint_processing=True,
            requires_confirmation=False,
        ))
    catalog = DatabaseConnectionCatalog()
    mcp_catalog = McpCatalog()
    runtime = get_mcp_runtime()
    confirmed = confirmed_tool_names or set()
    for connection in catalog.list_connections():
        if connection.status != "connected" or not runtime.is_connected(connection.mcp_server_id):
            continue
        server = mcp_catalog.get(connection.mcp_server_id)
        if server is None:
            continue
        execute_sql = next(
            (str(item.get("name")) for item in server.tools if str(item.get("name") or "").endswith("_execute_sql")),
            None,
        )
        if execute_sql is None:
            continue
        query_name, list_name, describe_name, execute_name = database_tool_names(connection)
        candidates = [
                Function(
                    name=query_name,
                    description=f"在 MySQL 连接「{connection.name}」执行一条只读 SQL，并返回真实结果。",
                    parameters=_sql_parameters("只读 SQL；仅允许 SELECT、SHOW、DESCRIBE 或 EXPLAIN"),
                    entrypoint=_query_entrypoint(runtime, connection.mcp_server_id, execute_sql),
                    skip_entrypoint_processing=True,
                    requires_confirmation=False,
                ),
                Function(
                    name=list_name,
                    description=f"列出 MySQL 连接「{connection.name}」当前数据库中的表和视图。",
                    parameters={"type": "object", "properties": {}, "additionalProperties": False},
                    entrypoint=_fixed_query_entrypoint(
                        runtime,
                        connection.mcp_server_id,
                        execute_sql,
                        "SHOW FULL TABLES",
                    ),
                    skip_entrypoint_processing=True,
                    requires_confirmation=False,
                ),
                Function(
                    name=describe_name,
                    description=f"查看 MySQL 连接「{connection.name}」中指定表的字段结构。",
                    parameters={
                        "type": "object",
                        "properties": {
                            "table": {"type": "string", "description": "表名，可使用 database.table"}
                        },
                        "required": ["table"],
                        "additionalProperties": False,
                    },
                    entrypoint=_describe_entrypoint(runtime, connection.mcp_server_id, execute_sql),
                    skip_entrypoint_processing=True,
                    requires_confirmation=False,
                ),
                Function(
                    name=execute_name,
                    description=(
                        f"在 MySQL 连接「{connection.name}」执行一条写入、DDL、事务或管理 SQL。"
                        "每次只允许一条语句，并且执行前必须获得用户确认。"
                    ),
                    parameters=_sql_parameters("需要写入数据库的一条 SQL"),
                    entrypoint=_write_entrypoint(runtime, connection.mcp_server_id, execute_sql),
                    skip_entrypoint_processing=True,
                    requires_confirmation=execute_name not in confirmed,
                ),
            ]
        for candidate in candidates:
            tool_id = f"database:{connection.connection_id}:{candidate.name}"
            if tool_registry.is_tool_enabled(tool_id):
                tools.append(candidate)
    return tools


def _sql_parameters(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": description}},
        "required": ["sql"],
        "additionalProperties": False,
    }


def _query_entrypoint(runtime: McpRuntimeManager, server_id: str, execute_sql: str):
    def call(sql: str):
        if classify_mysql_sql(sql) != "read":
            raise DailyAgentError("该 Tool 只允许只读 SQL；写操作请改用需要确认的 execute Tool。")
        return runtime.invoke(server_id, execute_sql, {"query": sql})

    return call


def _write_entrypoint(runtime: McpRuntimeManager, server_id: str, execute_sql: str):
    def call(sql: str):
        if classify_mysql_sql(sql) != "write":
            raise DailyAgentError("只读 SQL 请使用 query Tool，避免产生不必要的写操作确认。")
        return runtime.invoke(server_id, execute_sql, {"query": sql})

    return call


def _fixed_query_entrypoint(
    runtime: McpRuntimeManager,
    server_id: str,
    execute_sql: str,
    sql: str,
):
    def call():
        return runtime.invoke(server_id, execute_sql, {"query": sql})

    return call


def _describe_entrypoint(runtime: McpRuntimeManager, server_id: str, execute_sql: str):
    def call(table: str):
        normalized = str(table or "").strip()
        if not _TABLE_IDENTIFIER.fullmatch(normalized):
            raise DailyAgentError("表名格式不合法。")
        quoted = ".".join(f"`{part}`" for part in normalized.split("."))
        return runtime.invoke(server_id, execute_sql, {"query": f"DESCRIBE {quoted}"})

    return call
