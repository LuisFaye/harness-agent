from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ToolDefinition(BaseModel):
    tool_id: str = Field(description="工具稳定标识，包含来源类型和来源 ID")
    name: str = Field(description="提供给模型调用的工具名")
    description: str = Field(description="工具用途说明")
    source_type: Literal["skill", "mcp", "database", "workspace", "shell", "git", "artifact"] = Field(
        description="工具来源类型"
    )
    source_id: str = Field(description="来源 Skill 名称或 MCP Server ID")
    input_schema: dict[str, Any] = Field(default_factory=dict, description="工具输入 JSON Schema")
    risk_level: str = Field(default="low", description="风险等级")
    requires_confirmation: bool = Field(default=False, description="执行前是否需要用户确认")
    enabled: bool = Field(description="工具和来源是否均已启用")
    available: bool = Field(description="当前是否真的可以提供给 Agent")
    unavailable_reason: str | None = Field(default=None, description="不可调用时的中文原因")
    last_error: str | None = Field(default=None, description="来源最近一次错误")


class ToolListResponse(BaseModel):
    items: list[ToolDefinition] = Field(default_factory=list, description="真实 Tool 列表")
    total: int = Field(ge=0, description="工具总数")


class McpServerWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="MCP Server 名称")
    transport_type: Literal["stdio", "streamable_http"] = Field(description="连接方式")
    command: str | None = Field(default=None, max_length=500, description="stdio 启动命令")
    args: list[str] = Field(default_factory=list, description="stdio 命令参数")
    url: str | None = Field(default=None, max_length=1000, description="Streamable HTTP 地址")
    environment_keys: list[str] = Field(default_factory=list, description="允许传给 stdio 进程的环境变量键名")
    header_env_map: dict[str, str] = Field(
        default_factory=dict,
        description="HTTP 请求头到环境变量键名的映射；接口不接收也不返回密钥值",
    )
    enabled: bool = Field(default=True, description="是否启用该 MCP Server")

    @model_validator(mode="after")
    def validate_transport_fields(self):
        if self.transport_type == "stdio" and not str(self.command or "").strip():
            raise ValueError("stdio MCP 必须填写 command")
        if self.transport_type == "streamable_http" and not str(self.url or "").strip():
            raise ValueError("Streamable HTTP MCP 必须填写 url")
        return self


class McpServerResponse(BaseModel):
    server_id: str = Field(description="MCP Server 唯一标识")
    name: str
    transport_type: Literal["stdio", "streamable_http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    environment_keys: list[str] = Field(default_factory=list)
    header_env_map: dict[str, str] = Field(default_factory=dict)
    enabled: bool
    status: str = Field(description="disconnected、connecting、connected 或 error")
    last_connected_at: str | None = None
    last_error: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    prompts: list[dict[str, Any]] = Field(default_factory=list)


class McpCapabilityResponse(BaseModel):
    server_id: str
    status: str
    tools: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    prompts: list[dict[str, Any]] = Field(default_factory=list)


class McpResourceReadResponse(BaseModel):
    server_id: str = Field(description="MCP Server 唯一标识")
    uri: str = Field(description="本次读取的 Resource URI")
    contents: list[dict[str, Any]] = Field(default_factory=list, description="MCP 返回的文本或二进制资源内容")


class McpPromptRenderRequest(BaseModel):
    arguments: dict[str, str] = Field(default_factory=dict, description="Prompt 模板参数，键和值均按字符串传递")


class McpPromptRenderResponse(BaseModel):
    server_id: str = Field(description="MCP Server 唯一标识")
    name: str = Field(description="Prompt 名称")
    description: str | None = Field(default=None, description="MCP Server 返回的 Prompt 说明")
    messages: list[dict[str, Any]] = Field(default_factory=list, description="渲染后的 Prompt 消息列表")
