from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from daily_agent.artifact_models import ArtifactPublic
from daily_agent.citations.models import CitationBundle

RunStatusFilter = Literal[
    "queued",
    "running",
    "needs_input",
    "needs_confirmation",
    "success",
    "error",
    "max_iterations",
    "cancel_requested",
    "cancelled",
]


class RunUsage(BaseModel):
    """一次 Agent 运行的 Token 用量；没有可靠采集时字段保持为空。"""

    input_tokens: int | None = Field(default=None, description="模型输入 Token 数；未采集时为 null")
    output_tokens: int | None = Field(default=None, description="模型输出 Token 数；未采集时为 null")
    total_tokens: int | None = Field(default=None, description="本次运行总 Token 数；未采集时为 null")
    cache_read_tokens: int | None = Field(
        default=None,
        description="供应商上报的缓存读取 Token 数；未返回该字段时为 null，不要当作 0 处理",
    )
    cache_miss_tokens: int | None = Field(
        default=None,
        description="供应商上报的缓存未命中 Token 数；未返回该字段时为 null，不要当作 0 处理",
    )
    cache_write_tokens: int | None = Field(
        default=None,
        description="供应商上报的缓存写入 Token 数；未返回该字段时为 null，不要当作 0 处理",
    )
    reasoning_tokens: int | None = Field(
        default=None,
        description="供应商上报的推理（思考）Token 数；未返回该字段时为 null",
    )


class RunSummary(BaseModel):
    """运行监控列表使用的轻量摘要，不包含 Prompt、Trace 和完整工具参数。"""

    run_id: str = Field(description="一次 Agent 运行的唯一标识")
    user_id: str = Field(description="运行所属用户标识")
    session_id: str = Field(description="运行所属会话标识；旧记录缺失时为 unknown")
    status: str = Field(description="运行状态，例如 success、error、needs_input")
    query_summary: str = Field(description="用户输入摘要，最长 160 个字符")
    response_summary: str = Field(description="Agent 回答摘要，最长 240 个字符")
    execution_source: str = Field(description="执行来源，例如 agent_loop 或 skill_command")
    model_id: str | None = Field(default=None, description="模型 ID；旧记录未保存时为 null")
    error_stage: str | None = Field(default=None, description="失败阶段；非失败运行通常为 null")
    started_at: datetime | None = Field(default=None, description="运行开始时间，ISO 8601；旧记录可能为空")
    finished_at: datetime | None = Field(default=None, description="运行完成时间，ISO 8601；旧记录可能为空")
    duration_ms: int | None = Field(default=None, description="运行耗时毫秒数；未采集时为 null")
    total_tokens: int | None = Field(default=None, description="本次运行总 Token 数；未采集时为 null")
    has_detail: bool = Field(description="是否保存了可用于详情页的扩展数据")
    legacy_record: bool = Field(default=False, description="是否为字段不完整的旧运行记录")


class RunDetail(RunSummary):
    """运行详情；所有嵌套内容在返回前都必须经过脱敏和长度限制。"""

    query: str = Field(description="经过脱敏的用户输入")
    response_text: str = Field(description="经过脱敏的 Agent 最终回答；旧记录可能只有摘要")
    stop_reason: str | None = Field(default=None, description="运行停止原因")
    missing_info: list[str] = Field(default_factory=list, description="运行等待用户补充的信息")
    plan: dict[str, Any] | None = Field(default=None, description="执行计划；旧记录未保存时为 null")
    observations: list[dict[str, Any]] = Field(default_factory=list, description="脱敏后的 Tool 调用记录")
    trace: list[dict[str, Any]] = Field(default_factory=list, description="脱敏后的运行 Trace 时间线")
    prompt_snapshot: dict[str, Any] | None = Field(default=None, description="脱敏后的 Prompt 快照")
    model_requests: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "运行期间发给模型的 OpenAI 兼容 chat.completions 请求列表（按轮次）。"
            "仅 owner 可见；普通成员恒为空数组。"
            "正文来自本地短命存档（约保留 7 天且最多 50 次），不保证长期可查。"
        ),
    )
    working_state: dict[str, Any] | None = Field(default=None, description="当前任务的可回放工作状态")
    usage: RunUsage = Field(default_factory=RunUsage, description="本次运行 Token 用量")
    error_message: str | None = Field(default=None, description="脱敏后的错误信息")
    truncated_fields: list[str] = Field(
        default_factory=list,
        description="因内容过长被截断的字段路径；空数组表示没有截断",
    )
    artifacts: list[ArtifactPublic] = Field(
        default_factory=list,
        description="本次运行关联的 Artifact 公开记录；旧运行默认空数组",
    )
    citation_bundle: CitationBundle | None = Field(
        default=None,
        description=(
            "最终回答的段落级引用包；仅新产生且实际调用联网搜索的回答可能返回。"
            "status=partial 表示至少一个引用只能核对到搜索摘要，status=unavailable 表示引用核验失败但正文仍成功。"
        ),
    )


class RunListResponse(BaseModel):
    """Agent 运行分页列表。"""

    items: list[RunSummary]
    total: int = Field(ge=0, description="符合筛选条件的运行总数")
    limit: int = Field(ge=1, le=100, description="本页最多返回的记录数")
    offset: int = Field(ge=0, description="本页相对第一条记录的偏移量")


class RunEventItem(BaseModel):
    """一次运行中已经持久化的一条 NDJSON v2 事件。"""

    version: str = Field(description="流式协议版本，M2 固定为 2.0")
    run_id: str = Field(description="事件所属运行 ID")
    sequence: int = Field(ge=1, description="同一次运行内从 1 开始递增的事件序号")
    type: str = Field(description="事件类型，例如 response_delta、run_completed")
    timestamp: str = Field(description="事件产生时间，带时区的 ISO 8601 字符串")
    payload: dict[str, Any] = Field(default_factory=dict, description="事件业务数据；始终为 JSON 对象")


class RunEventListResponse(BaseModel):
    """按 sequence 补拉运行事件的响应。"""

    run_id: str = Field(description="本次查询的运行 ID")
    events: list[RunEventItem] = Field(default_factory=list, description="按 sequence 升序返回的事件")
    last_sequence: int = Field(ge=0, description="本次响应最后一条事件序号；没有事件时等于 after_sequence")
    has_more: bool = Field(description="是否还有未返回的后续事件")


class RunCancelRequest(BaseModel):
    """停止一次 Agent 运行的请求。"""

    user_id: str = Field(default="default", min_length=1, max_length=128, description="运行所属用户标识")


class RunCancelResponse(BaseModel):
    """停止请求的受理结果；最终是否停止以 status 和运行事件为准。"""

    run_id: str = Field(description="本次停止请求对应的 Agent 运行 ID")
    status: str = Field(description="当前运行状态，例如 cancel_requested、cancelled 或 success")
    accepted: bool = Field(description="是否接受停止请求；已结束的非取消运行返回 false")
    executor_notified: bool = Field(description="是否已把取消信号交给当前进程的 Agno 执行器")
    message: str = Field(description="给前端直接展示的中文结果说明")
