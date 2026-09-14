"""聊天请求与历史会话分页接口模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from daily_agent.memory import DEFAULT_SESSION_ID, DEFAULT_USER_ID


class ClientMessage(BaseModel):
    """前端当前页面已显示的最近消息，用于数据库历史不可用时兜底。"""

    role: Literal["user", "assistant", "system"]
    content: str = ""
    created_at: str | None = None


class AskRequest(BaseModel):
    """聊天请求体。"""

    query: str = Field(min_length=1, description="自然语言任务描述")
    user_id: str = DEFAULT_USER_ID
    session_id: str = DEFAULT_SESSION_ID
    client_messages: list[ClientMessage] = Field(default_factory=list)
    attachment_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="本轮需要读取的会话附件 ID",
    )
    suppress_user_message: bool = Field(
        default=False,
        description="安全表单完成后继续原任务时为 true，避免在会话中重复显示同一条用户消息",
    )


class PlanRequest(BaseModel):
    """规则规划请求体。"""

    query: str = Field(min_length=1, description="需要规划的原始需求")
    format: Literal["json", "markdown"] = "json"


class SessionSummaryResponse(BaseModel):
    """历史会话侧栏使用的轻量摘要。"""

    session_id: str = Field(description="会话唯一标识，可用于查询消息详情")
    title: str = Field(description="首次有效问答后由当前模型归纳的语义短标题；生成失败时回退首条消息，最多 36 字")
    preview: str = Field(description="会话最后一条消息的短预览")
    message_count: int = Field(ge=0, description="该会话当前持久化的消息总数")
    last_message_at: datetime | None = Field(default=None, description="最后一条消息时间")
    has_summary: bool = Field(description="是否已经生成历史压缩摘要")


class SessionListResponse(BaseModel):
    """会话分页查询结果。"""

    items: list[SessionSummaryResponse] = Field(description="当前页会话摘要，按最近活跃时间倒序")
    total: int = Field(ge=0, description="符合 user_id、keyword 与日期范围条件的会话总数")
    limit: int = Field(ge=1, le=100, description="本次请求的分页大小")
    offset: int = Field(ge=0, description="当前页相对全部匹配结果的起始位置")
