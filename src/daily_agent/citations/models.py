"""供应商无关的引用数据协议。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CitationSource(BaseModel):
    source_id: str = Field(description="本次运行内稳定的来源标识")
    source_type: Literal["web"] = Field(default="web", description="来源类型；第一期固定为 web")
    title: str = Field(description="搜索服务返回的来源标题")
    url: str = Field(description="搜索服务返回并经后端校验的 HTTP(S) 原文地址")
    domain: str = Field(description="来源 URL 的主机名")
    provider: str = Field(description="来源提供方；第一期为 bailian")
    tool_name: str = Field(description="产生该来源的真实 Tool 名称")
    retrieved_at: str = Field(description="来源进入本次运行注册表的 ISO 8601 时间")
    content_level: Literal["metadata", "snippet", "passage", "fulltext"] = Field(
        description="证据内容强度；snippet 仅表示搜索摘要，passage 表示已读取网页正文"
    )
    source_role: Literal["primary", "secondary", "unknown"] = Field(
        default="unknown",
        description="来源权威性；第一期无法可靠判断时为 unknown",
    )
    content_hash: str | None = Field(default=None, description="当前可用证据文本的 SHA-256 摘要")
    evidence_id: str | None = Field(default=None, description="关联原始 Tool Evidence 的标识；没有时为 null")


class CitationChunk(BaseModel):
    chunk_id: str = Field(description="本次运行内稳定的证据块标识")
    source_id: str = Field(description="证据块所属来源标识")
    text: str = Field(description="可供用户核对的搜索摘要或网页正文原文")
    locator: dict[str, Any] = Field(description="证据在来源中的定位信息")
    content_hash: str = Field(description="证据块文本的 SHA-256 摘要")


class CitationSupport(BaseModel):
    support_id: str = Field(description="回答块与证据块之间的稳定关系标识")
    block_id: str = Field(description="最终 Markdown 语义块标识")
    answer_quote: str = Field(description="用于刷新后重新校验锚点的回答块纯文本")
    target_start: int | None = Field(default=None, description="未来句子级引用的块内起始位置")
    target_end: int | None = Field(default=None, description="未来句子级引用的块内结束位置")
    chunk_ids: list[str] = Field(description="实际支持该回答块的证据块标识，最多三个")
    support_status: Literal["supported", "partial", "unsupported"] = Field(
        description="正文证据为 supported，只有搜索摘要时为 partial"
    )
    origin: Literal["provider_native", "agent_generated", "model_aligned", "deterministic"] = Field(
        description="支持关系的产生方式"
    )


class CitationBundle(BaseModel):
    version: Literal["1.0"] = Field(default="1.0", description="引用协议版本")
    status: Literal["complete", "partial", "unavailable"] = Field(
        description="本次回答引用解析和确定性校验的完成状态"
    )
    answer_hash: str = Field(description="最终回答 Markdown 的 SHA-256 摘要")
    sources: list[CitationSource] = Field(default_factory=list, description="本次回答实际引用的来源")
    chunks: list[CitationChunk] = Field(default_factory=list, description="本次回答实际引用的证据块")
    supports: list[CitationSupport] = Field(default_factory=list, description="回答块到证据块的支持关系")

    @classmethod
    def unavailable(cls, answer_text: str) -> "CitationBundle":
        return cls(status="unavailable", answer_hash=content_hash(answer_text))


def content_hash(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


class AgentAnswerBlock(BaseModel):
    markdown: str = Field(
        min_length=1,
        description="一个完整的 Markdown 语义块，例如普通段落、单个列表项、标题或代码块",
    )
    source_ids: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="直接支持当前块的来源 ID；只能使用搜索工具结果中由服务端提供的 source_id",
    )


class AgentStructuredAnswer(BaseModel):
    blocks: list[AgentAnswerBlock] = Field(
        min_length=1,
        description="按最终展示顺序排列的回答块；正文由各块 markdown 使用两个换行拼接",
    )
