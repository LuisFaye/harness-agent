from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DatabaseConnectionWriteRequest(BaseModel):
    """创建或更新 MySQL 连接时使用的安全请求体。"""

    name: str = Field(min_length=1, max_length=64, description="连接别名，例如 mysql-front")
    host: str = Field(min_length=1, max_length=255, description="MySQL 主机名或 IP")
    port: int = Field(default=3306, ge=1, le=65535, description="MySQL 端口")
    database: str = Field(min_length=1, max_length=128, description="默认数据库名称")
    username: str = Field(min_length=1, max_length=128, description="MySQL 登录用户名")
    password: str = Field(default="", max_length=4096, description="密码仅写入本地私密文件，接口不会回显")
    interaction_request_id: str | None = Field(
        default=None,
        max_length=128,
        description="聊天内安全输入请求 ID；提供后会在连接成功后继续原任务",
    )


class DatabaseConnectionResponse(BaseModel):
    """不包含密码、envfile 路径或完整连接串的数据库连接视图。"""

    connection_id: str
    name: str
    driver: Literal["mysql"] = "mysql"
    host: str
    port: int
    database: str
    username: str
    status: str
    last_connected_at: str | None = None
    last_error: str | None = None
    imported: bool
    password_configured: bool
    scope: Literal["agent"] = "agent"
    tool_names: list[str] = Field(default_factory=list)


class DatabaseConnectionListResponse(BaseModel):
    items: list[DatabaseConnectionResponse] = Field(default_factory=list)
    total: int = Field(ge=0)


class DatabaseConnectionSaveResponse(BaseModel):
    connection: DatabaseConnectionResponse
    continuation_query: str | None = Field(
        default=None,
        description="不含密钥的原任务；前端用它自动继续，且不重复显示用户消息",
    )


class DatabaseConnectionInteraction(BaseModel):
    type: Literal["database_connection"] = "database_connection"
    request_id: str
    title: str = "连接 MySQL"
    description: str = "密码通过安全表单直达本机后端，不会发送给模型。"
    name: str = ""
    host: str = ""
    port: int = 3306
    database: str = ""
    username: str = ""
    scope: Literal["agent"] = "agent"
