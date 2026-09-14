"""Artifact 公开数据模型。

本模块只定义对外暴露的模型：REST 请求体、响应模型与公开记录。
Artifact 的存储路径、根目录等主机绝对路径绝不进入这些模型，保证任何公开模型、
API、Tool 输出与日志都不会泄露 storage_path 或 Artifact 根目录。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# 文本产物：可走 artifact_create_text，预览用 Markdown/纯文本渲染。
TEXT_SUFFIXES = (".md", ".txt", ".csv", ".json", ".html", ".htm", ".svg")
# 二进制交付物：只能从工作区快照登记，不能用 create_text 伪造扩展名。
BINARY_SUFFIXES = (".docx", ".pdf", ".xlsx", ".png", ".jpg", ".jpeg", ".webp")
# 登记与下载允许的全部扩展名。
SUPPORTED_SUFFIXES = TEXT_SUFFIXES + BINARY_SUFFIXES

# source_kind 的两种取值：Agent 直接生成的文本，或工作区文件的不可变快照。
SourceKind = Literal["generated_text", "workspace_snapshot"]


class ArtifactPublic(BaseModel):
    """一条 Artifact 的公开元数据。

    该模型不含正文、不含 storage_path 与任何主机绝对路径；下载地址是相对 URL，
    由前端配合 API 基地址使用。
    """

    artifact_id: str = Field(
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
        description="32 位小写十六进制产物 ID",
    )
    user_id: str = Field(description="产物所属用户标识")
    session_id: str = Field(description="产物所属会话标识")
    run_id: str = Field(description="产物所属 Agent 运行 ID；无运行上下文时为当前实例私有 owner id")
    filename: str = Field(description="产物文件名（仅 basename，不含目录）")
    suffix: str = Field(description="扩展名，例如 .md、.docx、.pdf")
    content_type: str = Field(description="下载时使用的 MIME 类型")
    size: int = Field(ge=1, description="产物文件字节数")
    sha256: str = Field(description="产物内容 SHA-256 校验和（小写十六进制）")
    summary: str | None = Field(default=None, max_length=500, description="单行摘要，最多 500 字符")
    source_kind: SourceKind = Field(description="generated_text=Agent 生成文本；workspace_snapshot=工作区文件快照")
    source_path: str | None = Field(
        default=None,
        description="工作区快照登记时的 Workspace Root 相对路径；生成文本为 null",
    )
    created_at: datetime = Field(description="创建时间，ISO 8601")
    retained: bool = Field(description="是否保留；保留时永不过期")
    expires_at: datetime | None = Field(
        default=None,
        description="未保留时的过期时间：创建后 30 天，取消保留时从当前时间重新计算 30 天",
    )
    download_url: str = Field(
        description=(
            "可直接使用的相对下载地址，例如 /artifacts/{artifact_id}/download?user_id=...；"
            "user_id 已做 URL 编码，非 default 用户也可直接访问"
        )
    )


class ArtifactListResponse(BaseModel):
    """Artifact 分页列表，按创建时间倒序。"""

    items: list[ArtifactPublic] = Field(default_factory=list, description="本页 Artifact 公开记录")
    total: int = Field(ge=0, description="符合筛选条件的 Artifact 总数")
    limit: int = Field(ge=1, le=100, description="本页最多返回的记录数")
    offset: int = Field(ge=0, description="本页相对第一条记录的偏移量")


class ArtifactRetentionRequest(BaseModel):
    """修改一条 Artifact 保留状态的请求体。"""

    retained: bool = Field(
        description="true=永久保留（expires_at 为 null）；false=从当前时间重新计算 30 天过期时间",
    )


class ArtifactDeleteResponse(BaseModel):
    """删除一条 Artifact 的结果。"""

    artifact_id: str = Field(description="被删除的产物 ID")
    deleted: bool = Field(description="是否已删除；重复删除返回 404，不会返回 deleted=false")
