"""FastAPI 对外入口，同时提供 HTTP API 和浏览器工作台。"""
from __future__ import annotations
import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import date as date_type
from pathlib import Path
from typing import Literal
from fastapi import Depends, FastAPI, File, Form, HTTPException, Path as PathParam, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, Field
from daily_agent.app_context import ApplicationContext
from daily_agent.config import api_port
from daily_agent.agent import prewarm_agent_runtime
from daily_agent.auth_dependencies import get_auth_service, require_owner, require_user
from daily_agent.access_policy import sanitize_member_skill_summary
from daily_agent.auth_models import AuthenticatedUser
from daily_agent.auth_routes import register_auth_routes
from daily_agent.auth_service import AuthService
from daily_agent.api_serializers import (
    sanitize_member_result as _sanitize_member_result,
    secure_input_result as _secure_input_result,
    serialize_ask_result as _serialize_ask_result,
)
from daily_agent.artifact_models import ArtifactPublic
from daily_agent.artifact_routes import register_artifact_routes
from daily_agent.artifact_service import ArtifactService
from daily_agent.attachment_service import AttachmentService, MAX_ATTACHMENT_BYTES
from daily_agent.model_provider import model_supports_vision
from daily_agent.capability_routes import register_capability_routes
from daily_agent.config import DEFAULT_CONTEXT_TOKEN_BUDGET
from daily_agent.database_connections import (
    DatabaseConnectionService,
    DatabaseInteractionStore,
    sanitize_database_credentials,
)
from daily_agent.errors import DailyAgentError, ErrorPayload, MemoryError as DailyAgentMemoryError
from daily_agent.logging_setup import bind_log_context, clear_log_context, log_level_name, setup_logging
from daily_agent.memory import DEFAULT_SESSION_ID, DEFAULT_USER_ID
from daily_agent.run_activity import register_run_activity_route
from daily_agent.run_models import (
    RunCancelRequest,
    RunCancelResponse,
    RunDetail,
    RunEventListResponse,
    RunListResponse,
    RunStatusFilter,
)
from daily_agent.sharing_routes import register_sharing_routes
from daily_agent.sharing_service import SharingService
from daily_agent.runtime_state import generate_run_id
from daily_agent.session_api_models import AskRequest, PlanRequest, SessionListResponse
from daily_agent.services import (
    AskService,
    ConversationService,
    DailyReportService,
    HealthService,
    HistoryService,
    ModelConfigService,
    McpService,
    PlanningService,
    PreferenceService,
    RunLifecycleService,
    RunService,
    SchedulerService,
    SkillCatalogService,
    ToolService,
)
from daily_agent.stream_delivery import AskCapacity, StreamDelivery
from daily_agent.stream_protocol import StreamEventSequencer
from daily_agent.runtime_version import build_runtime_metadata
from daily_agent.web_assets import (
    frontend_build_metadata,
    mount_workbench_frontends,
)
from daily_agent.user_workspace import resolve_user_workspace_path
WEB_DIR = Path(__file__).resolve().parent / "web"
NEXT_WEB_DIR = Path(__file__).resolve().parent / "web_dist"
logger = logging.getLogger(__name__)

class PreferenceWriteRequest(BaseModel):
    """写入用户偏好的请求体。"""
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    user_id: str = DEFAULT_USER_ID

class ReportGenerateRequest(BaseModel):
    """手工生成日报的请求体。"""
    repos: list[str] = Field(min_length=1, description="日报扫描的仓库路径列表")
    date: date_type
    author: str | None = None
    extra_notes: list[str] = Field(default_factory=list)

class ScheduleReportRequest(BaseModel):
    """创建“定时日报任务”的请求体。"""
    time: str = Field(description="每天执行时间，格式 HH:MM")
    repos: list[str] = Field(min_length=1)
    title: str | None = None
    author: str | None = None
    extra_notes: list[str] = Field(default_factory=list)
    timezone: str = "Asia/Shanghai"
    user_id: str = DEFAULT_USER_ID

class ModelConfigWriteRequest(BaseModel):
    """更新模型配置的请求体。"""
    provider: Literal["openai_compatible", "dashscope"] = Field(
        default="openai_compatible",
        description="模型 Provider；OpenAI、DeepSeek 和兼容网关请选择 openai_compatible",
    )
    model_id: str = Field(min_length=1, description="模型 ID，例如 deepseek-v4-flash")
    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("api_key", "dashscope_api_key"),
        description="模型 API Key；留空保留原 Key，不会从查询接口回显",
    )
    base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices("base_url", "dashscope_base_url"),
        description="模型 Provider 的 API Base URL",
    )
    clear_api_key: bool = Field(default=False, description="是否明确清空已保存的 API Key")
    agno_telemetry: bool = Field(default=False, description="是否允许 Agno 发送运行遥测；默认关闭")
    thinking_enabled: bool = Field(default=False, description="是否开启模型深度思考模式")
    reasoning_effort: Literal["high", "max"] = Field(default="high", description="思考强度；复杂任务可选择 max")
    context_token_budget: int = Field(
        default=DEFAULT_CONTEXT_TOKEN_BUDGET,
        ge=1,
        description="注入模型的上下文 token 预算；默认按 DeepSeek V4 的 1M 上下文窗口配置",
    )

class SkillImportRequest(BaseModel):
    """导入 skill 的请求体。"""
    path: str = Field(default="", description="本地 skill 目录、GitHub URL，或 skills.sh 单个 Skill 地址")
    source_url: str | None = Field(default=None, description="GitHub URL；不传时会尝试从 path 自动识别")
    enable: bool = True

def _error_payload_from_exception(exc: Exception, run_id: str, stage: str = "ask") -> dict:
    if isinstance(exc, DailyAgentError):
        payload = exc.payload.to_dict()
        payload["run_id"] = run_id
        return payload
    return {
        "code": "stream_execution_error" if stage == "ask_stream" else "ask_execution_error",
        "message": str(exc),
        "stage": stage,
        "run_id": run_id,
        "details": {},
        "retriable": False,
    }

def _terminal_event_payload(event_type: str, result: dict) -> dict:
    # 模型请求只落本地短命文件，流式终态与 MySQL metadata 都不携带全文。
    slim_result = {key: value for key, value in result.items() if key != "model_requests"}
    if event_type == "run_cancelled":
        return {
            "status": "cancelled",
            "reason": str(result.get("stop_reason") or "用户取消任务"),
            "result": slim_result,
        }
    return {"status": str(result.get("status") or "error"), "result": slim_result}


def _prepare_attachment_inputs(
    attachment_service: AttachmentService,
    attachment_ids: list[str],
    *,
    user_id: str,
    session_id: str,
    vision_enabled: bool,
) -> tuple[str, list]:
    """组装附件文本上下文与视觉模型可直接消费的图片列表。"""
    attachment_context = attachment_service.build_context(
        attachment_ids,
        user_id=user_id,
        session_id=session_id,
        vision_enabled=vision_enabled,
    )
    attachment_images = (
        attachment_service.build_vision_images(
            attachment_ids,
            user_id=user_id,
            session_id=session_id,
        )
        if vision_enabled
        else []
    )
    return attachment_context, attachment_images

def _working_phase_from_run_status(status: str) -> str:
    return {
        "running": "executing",
        "queued": "preparing",
        "success": "completed",
        "needs_input": "waiting_input",
        "needs_confirmation": "waiting_confirmation",
        "cancel_requested": "cancelled",
        "cancelled": "cancelled",
    }.get(str(status or ""), "failed")

def create_app(
        *,
        ask_service: AskService | None = None,
        planning_service: PlanningService | None = None,
        history_service: HistoryService | None = None,
        preference_service: PreferenceService | None = None,
        health_service: HealthService | None = None,
        scheduler_service: SchedulerService | None = None,
        report_service: DailyReportService | None = None,
        conversation_service: ConversationService | None = None,
        skill_catalog_service: SkillCatalogService | None = None,
        model_config_service: ModelConfigService | None = None,
        run_service: RunService | None = None,
        run_lifecycle_service: RunLifecycleService | None = None,
        tool_service: ToolService | None = None,
        mcp_service: McpService | None = None,
        database_connection_service: DatabaseConnectionService | None = None,
        attachment_service: AttachmentService | None = None,
        artifact_service: ArtifactService | None = None,
        auth_service: AuthService | None = None,
        app_context: ApplicationContext | None = None,
) -> FastAPI:
    """组装 API 应用。
    所有 service 都允许注入，主要是为了测试时可以替换成 mock/stub；
    线上或本地直接运行时则走默认实现。
    """
    app_context = app_context or ApplicationContext.build()
    ask_service = ask_service or AskService(settings=app_context.settings)
    planning_service = planning_service or PlanningService(app_context=app_context)
    history_service = history_service or HistoryService()
    preference_service = preference_service or PreferenceService()
    health_service = health_service or HealthService(app_context=app_context)
    scheduler_service = scheduler_service or SchedulerService()
    report_service = report_service or DailyReportService()
    conversation_service = conversation_service or ConversationService(app_context=app_context)
    skill_catalog_service = skill_catalog_service or SkillCatalogService(app_context=app_context)
    model_config_service = model_config_service or ModelConfigService(app_context=app_context)
    run_service = run_service or RunService()
    run_lifecycle_service = run_lifecycle_service or RunLifecycleService(async_event_persistence=True)
    tool_service = tool_service or ToolService()
    mcp_service = mcp_service or McpService()
    database_connection_service = database_connection_service or DatabaseConnectionService()
    attachment_service = attachment_service or AttachmentService()
    artifact_service = artifact_service or ArtifactService()
    auth_service = auth_service or AuthService()
    sharing_service = SharingService(store=auth_service.store)
    ask_capacity = AskCapacity()
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await asyncio.to_thread(auth_service.init_schema)
        adopt_databases = getattr(database_connection_service, "adopt_legacy_mysql_servers", None)
        if callable(adopt_databases):
            await asyncio.to_thread(adopt_databases)
        reconnect = getattr(mcp_service, "connect_enabled_servers", None)
        if callable(reconnect):
            await asyncio.to_thread(reconnect)
        yield
        await asyncio.to_thread(run_lifecycle_service.shutdown_event_writer)
    # 公网运行默认不暴露接口枚举页面；所有者本地排障时可以显式临时开启。
    api_docs_enabled = os.getenv("SB_AGENT_ENABLE_API_DOCS", "").strip().lower() in {"1", "true", "yes", "on"}
    app = FastAPI(
        title="SB Agent API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if api_docs_enabled else None,
        redoc_url="/redoc" if api_docs_enabled else None,
        openapi_url="/openapi.json" if api_docs_enabled else None,
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        # 这些响应头覆盖 SPA 和 API，减少被第三方页面嵌入、MIME 猜测及来源信息外带。
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'self'; "
            "connect-src 'self'; "
            "font-src 'self' data:; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data: blob: https:; "
            "object-src 'none'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'"
        )
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        if request.url.scheme == "https" or forwarded_proto == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    register_auth_routes(
        app,
        auth_service=auth_service,
        legacy_user_claimers=[attachment_service.claim_legacy_user, artifact_service.claim_legacy_user],
    )
    @app.exception_handler(DailyAgentMemoryError)
    async def handle_daily_agent_memory_error(request, exc: DailyAgentMemoryError):
        logger.error("DailyAgentMemoryError: %s %s - %s", request.method, request.url.path, exc, exc_info=True)
        return JSONResponse(status_code=503, content={"error": exc.payload.to_dict()})
    @app.exception_handler(DailyAgentError)
    async def handle_daily_agent_error(request, exc: DailyAgentError):
        logger.warning("DailyAgentError: %s %s - %s", request.method, request.url.path, exc, exc_info=True)
        return JSONResponse(status_code=400, content={"error": exc.payload.to_dict()})
    @app.exception_handler(ValueError)
    async def handle_value_error(request, exc: ValueError):
        logger.warning("ValueError: %s %s - %s", request.method, request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=400,
            content={
                "error": ErrorPayload(
                    code="value_error",
                    message=str(exc),
                    stage="request_validation",
                    run_id="-",
                ).to_dict()
            },
        )
    @app.get("/health", summary="检查服务是否存活")
    def health():
        return {"status": "ok"}

    @app.get("/health/details", summary="查看服务详细健康状态")
    def health_details(_: AuthenticatedUser = Depends(require_owner)):
        checks = health_service.run_checks()
        statuses = {item["status"] for item in checks}
        status = "error" if "error" in statuses else "degraded" if "warn" in statuses else "ok"
        return {
            "status": status,
            "checks": checks,
            "runtime": build_runtime_metadata(app_context.paths.workspace_root),
            "frontend": {
                **frontend_build_metadata(NEXT_WEB_DIR),
                "default_entry": "/",
                "legacy_entry": "/?legacy=1",
            },
        }
    @app.post("/attachments")
    async def upload_attachment(
            file: UploadFile = File(description="最大 20MB，支持 PDF、Word、Excel、文本和常见图片"),
            session_id: str = Form(default=DEFAULT_SESSION_ID),
            user: AuthenticatedUser = Depends(require_user),
    ):
        content = await file.read(MAX_ATTACHMENT_BYTES + 1)
        record = attachment_service.save(
            filename=file.filename or "attachment",
            content_type=file.content_type or "application/octet-stream",
            content=content,
            user_id=user.id,
            session_id=session_id,
        )
        return record.to_public_dict(include_text=True)
    @app.get("/attachments")
    def list_attachments(
            session_id: str = DEFAULT_SESSION_ID,
            user: AuthenticatedUser = Depends(require_user),
    ):
        return {
            "items": [
                item.to_public_dict()
                for item in attachment_service.list_session(user_id=user.id, session_id=session_id)
            ]
        }
    @app.get("/attachments/{attachment_id}/download")
    def download_attachment(
            attachment_id: str,
            session_id: str = DEFAULT_SESSION_ID,
            user: AuthenticatedUser = Depends(require_user),
    ):
        record = attachment_service.get(attachment_id, user_id=user.id, session_id=session_id)
        return FileResponse(record.storage_path, media_type=record.content_type, filename=record.filename)
    # 聊天与规则规划。
    @app.post("/ask")
    def ask(request: AskRequest, user: AuthenticatedUser = Depends(require_user)):
        with ask_capacity.slot():
            logger.info("收到 /ask 请求")
            request.user_id = user.id
            effective_query, exposed_credentials = (
                sanitize_database_credentials(request.query)
                if user.is_owner
                else (request.query, None)
            )
            request.query = effective_query
            request_run_id = generate_run_id("ask")
            sequencer = StreamEventSequencer(request_run_id)
            try:
                run_lifecycle_service.start_run(
                    run_id=request_run_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    query_text=request.query,
                    persist_user_message=not request.suppress_user_message,
                    attachment_ids=request.attachment_ids,
                )
                run_lifecycle_service.record_event(sequencer.next("run_started", {"status": "running"}))
            except DailyAgentMemoryError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "run_store_unavailable", "message": str(exc)},
                ) from exc
            vision_enabled = model_supports_vision(app_context.settings.model_id)
            attachment_context, attachment_images = _prepare_attachment_inputs(
                attachment_service,
                request.attachment_ids,
                user_id=request.user_id,
                session_id=request.session_id,
                vision_enabled=vision_enabled,
            )
            # 客户端回显的最近聊天记录交给 AskService：只有那里能同时看到原生历史，
            # 从而丢掉与历史逐字重复的部分，避免同一段正文被发送两次。
            extra_context = attachment_context
            try:
                if exposed_credentials is not None:
                    interaction = DatabaseInteractionStore().create(
                        user_id=request.user_id,
                        session_id=request.session_id,
                        query=request.query,
                        fields=exposed_credentials,
                    )
                    serialized_result = _secure_input_result(request_run_id, interaction.model_dump())
                    serialized_result, _ = run_lifecycle_service.complete_run(
                        run_id=request_run_id,
                        user_id=request.user_id,
                        result=serialized_result,
                        terminal_event_factory=lambda event_type, final_result: sequencer.next(
                            event_type,
                            _terminal_event_payload(event_type, final_result),
                        ),
                    )
                    return serialized_result
                result = ask_service.run(
                    query=request.query,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=request_run_id,
                    extra_context_instructions=extra_context,
                    attachment_images=attachment_images,
                    attachment_ids=request.attachment_ids,
                    suppress_user_message=request.suppress_user_message,
                    user_message_persisted=not request.suppress_user_message,
                    client_messages=request.client_messages,
                )
                serialized_result = _serialize_ask_result(result)
                if not user.is_owner:
                    serialized_result = _sanitize_member_result(serialized_result)
                serialized_result["run_id"] = request_run_id
                response_payload = {"text": serialized_result["response_text"]}
                if serialized_result.get("citation_bundle") is not None:
                    response_payload["citation_bundle"] = serialized_result["citation_bundle"]
                run_lifecycle_service.record_event(sequencer.next("response_completed", response_payload))
                serialized_result, _ = run_lifecycle_service.complete_run(
                    run_id=request_run_id,
                    user_id=request.user_id,
                    result=serialized_result,
                    terminal_event_factory=lambda event_type, final_result: sequencer.next(
                        event_type,
                        _terminal_event_payload(event_type, final_result),
                    ),
                )
                return serialized_result
            except Exception as exc:
                error_payload = _error_payload_from_exception(exc, request_run_id)
                terminal_event = sequencer.next("run_failed", error_payload)
                try:
                    run_lifecycle_service.fail_run(
                        run_id=request_run_id,
                        user_id=request.user_id,
                        error_payload=error_payload,
                        terminal_event=terminal_event,
                    )
                except DailyAgentError:
                    logger.exception("同步 ask 失败后无法更新运行终态：run_id=%s", request_run_id)
                if isinstance(exc, DailyAgentError):
                    status_code = 503 if isinstance(exc, DailyAgentMemoryError) else 400
                    return JSONResponse(status_code=status_code, content={"error": error_payload})
                raise
    @app.post("/ask/stream")
    def ask_stream(request: AskRequest, user: AuthenticatedUser = Depends(require_user)):
        ask_capacity.acquire()
        worker_started = False
        try:
            logger.info("收到 /ask/stream 请求")
            request.user_id = user.id
            effective_query, exposed_credentials = (
                sanitize_database_credentials(request.query)
                if user.is_owner
                else (request.query, None)
            )
            request.query = effective_query
            request_run_id = generate_run_id("ask")
            message_queue = StreamDelivery()
            sequencer = StreamEventSequencer(request_run_id)
            stream_state: dict[str, object] = {"response_started": False, "response_replace_text": None}
            try:
                run_lifecycle_service.start_run(
                    run_id=request_run_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    query_text=request.query,
                    persist_user_message=not request.suppress_user_message,
                    attachment_ids=request.attachment_ids,
                )
            except DailyAgentMemoryError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "run_store_unavailable", "message": str(exc)},
                ) from exc
            def enqueue_event(event_type: str, payload: dict | None = None) -> None:
                event = sequencer.next(event_type, payload)
                message_queue.put(event.to_ndjson())
                run_lifecycle_service.schedule_event(event)
            def emit_stream_event(event_type: str, payload: dict) -> None:
                if event_type == "response_started":
                    if not stream_state["response_started"]:
                        stream_state["response_started"] = True
                        enqueue_event("response_started", payload)
                    return
                if event_type == "response_delta":
                    if not stream_state["response_started"]:
                        stream_state["response_started"] = True
                        enqueue_event("response_started")
                    delta_payload: dict[str, object] = {"delta": str(payload.get("delta") or "")}
                    if payload.get("text"):
                        delta_payload["text"] = str(payload.get("text") or "")
                    if payload.get("citation_bundle") is not None:
                        delta_payload["citation_bundle"] = payload["citation_bundle"]
                    enqueue_event("response_delta", delta_payload)
                    return
                if event_type == "response_block":
                    if not stream_state["response_started"]:
                        stream_state["response_started"] = True
                        enqueue_event("response_started")
                    enqueue_event("response_block", payload)
                    return
                if event_type == "response_replace":
                    stream_state["response_replace_text"] = str(payload.get("text") or "")
                    return
                # 第一批先兼容服务层可能增加的标准事件；未知事件不能污染公开协议。
                if event_type in {"tool_started", "tool_finished", "status_updated"}:
                    enqueue_event(event_type, payload)
            def emit_response_text(text: str, citation_bundle: dict | None = None) -> None:
                final_text = text or ""
                payload = {"text": final_text}
                if citation_bundle is not None:
                    payload["citation_bundle"] = citation_bundle
                enqueue_event("response_completed", payload)
            enqueue_event("run_started", {"status": "running"})
            run_lifecycle_service.bind_cancel_notifier(
                run_id=request_run_id,
                notifier=lambda: enqueue_event("run_cancel_requested", {"status": "cancel_requested"}),
            )
            # 这里用后台线程执行 ask，把阶段事件先写进队列；
            # 前端用 fetch 读取 NDJSON，就能边收边渲染“处理中”的时间线。
            def worker() -> None:
                try:
                    bind_log_context(
                        run_id=request_run_id,
                        user_id=request.user_id,
                        session_id=request.session_id,
                        process_role="api",
                    )
                    if exposed_credentials is not None:
                        interaction = DatabaseInteractionStore().create(
                            user_id=request.user_id,
                            session_id=request.session_id,
                            query=request.query,
                            fields=exposed_credentials,
                        )
                        serialized_result = _secure_input_result(request_run_id, interaction.model_dump())
                        emit_response_text(serialized_result["response_text"])
                        enqueue_event(
                            "run_waiting",
                            {
                                "status": "needs_input",
                                "missing_info": [],
                                "interaction": serialized_result["interaction"],
                            },
                        )
                        serialized_result, terminal_event = run_lifecycle_service.complete_run(
                            run_id=request_run_id,
                            user_id=request.user_id,
                            result=serialized_result,
                            terminal_event_factory=lambda event_type, final_result: sequencer.next(
                                event_type,
                                _terminal_event_payload(event_type, final_result),
                            ),
                        )
                        message_queue.put(terminal_event.to_ndjson())
                        return
                    vision_enabled = model_supports_vision(app_context.settings.model_id)
                    attachment_context, attachment_images = _prepare_attachment_inputs(
                        attachment_service,
                        request.attachment_ids,
                        user_id=request.user_id,
                        session_id=request.session_id,
                        vision_enabled=vision_enabled,
                    )
                    # 客户端回显去重在 AskService 里完成（那里才有原生历史）。
                    extra_context = attachment_context
                    result = ask_service.run(
                        query=request.query,
                        user_id=request.user_id,
                        session_id=request.session_id,
                        run_id=request_run_id,
                        trace_callback=lambda event: enqueue_event("trace_updated", {"trace": event.to_metadata()}),
                        stream_callback=emit_stream_event,
                        extra_context_instructions=extra_context,
                        attachment_images=attachment_images,
                        attachment_ids=request.attachment_ids,
                        cancellation_check=lambda: run_lifecycle_service.is_cancel_requested(
                            request_run_id,
                            user_id=request.user_id,
                        ),
                        suppress_user_message=request.suppress_user_message,
                        user_message_persisted=not request.suppress_user_message,
                        client_messages=request.client_messages,
                    )
                    serialized_result = _serialize_ask_result(result)
                    if not user.is_owner:
                        serialized_result = _sanitize_member_result(serialized_result)
                    serialized_result["run_id"] = request_run_id
                    if not (
                        serialized_result["status"] == "cancelled"
                        or run_lifecycle_service.is_cancel_requested(request_run_id, user_id=request.user_id)
                    ):
                        emit_response_text(
                            str(stream_state["response_replace_text"] or result.response_text),
                            serialized_result.get("citation_bundle"),
                        )
                    if serialized_result["status"] in {"needs_input", "needs_confirmation"}:
                        enqueue_event(
                            "run_waiting",
                            {
                                "status": serialized_result["status"],
                                "missing_info": serialized_result["missing_info"],
                                "interaction": serialized_result.get("interaction"),
                            },
                        )
                    serialized_result, terminal_event = run_lifecycle_service.complete_run(
                        run_id=request_run_id,
                        user_id=request.user_id,
                        result=serialized_result,
                        terminal_event_factory=lambda event_type, final_result: sequencer.next(
                            event_type,
                            _terminal_event_payload(event_type, final_result),
                        ),
                    )
                    message_queue.put(terminal_event.to_ndjson())
                except Exception as exc:  # pragma: no cover - 线程内异常只能通过流回传
                    logger.exception(
                        "Ask stream failed: user_id=%s session_id=%s",
                        request.user_id,
                        request.session_id,
                    )
                    error_payload = _error_payload_from_exception(exc, request_run_id, "ask_stream")
                    try:
                        if run_lifecycle_service.is_cancel_requested(request_run_id, user_id=request.user_id):
                            cancelled_result = {
                                "run_id": request_run_id,
                                "status": "cancelled",
                                "execution_source": "agent_loop",
                                "error_stage": "cancellation",
                                "response_text": "任务已取消。",
                                "stop_reason": "用户取消任务",
                                "missing_info": [],
                                "plan": None,
                                "observations": [],
                                "extracted_memories": [],
                                "trace": [],
                                "prompt_snapshot": None,
                            }
                            _, terminal_event = run_lifecycle_service.complete_run(
                                run_id=request_run_id,
                                user_id=request.user_id,
                                result=cancelled_result,
                                terminal_event_factory=lambda event_type, final_result: sequencer.next(
                                    event_type,
                                    _terminal_event_payload(event_type, final_result),
                                ),
                            )
                        else:
                            terminal_event = sequencer.next("run_failed", error_payload)
                            run_lifecycle_service.fail_run(
                                run_id=request_run_id,
                                user_id=request.user_id,
                                error_payload=error_payload,
                                terminal_event=terminal_event,
                            )
                    except DailyAgentError:
                        logger.exception("流式 ask 失败后无法更新运行终态：run_id=%s", request_run_id)
                    message_queue.put(terminal_event.to_ndjson())
                finally:
                    clear_log_context()
                    bind_log_context(process_role="api")
                    ask_capacity.release()
                    message_queue.put(None)
            threading.Thread(target=worker, daemon=True, name="sb-agent-ask").start()
            worker_started = True
            return StreamingResponse(
                message_queue.iterate(),
                media_type="application/x-ndjson",
                headers={"Cache-Control": "no-cache", "X-Run-Id": request_run_id},
            )
        finally:
            # 启动成功后由工作线程归还名额，客户端断开不能提前释放执行容量。
            if not worker_started:
                ask_capacity.release()
    @app.post("/plan")
    def plan(request: PlanRequest, _: AuthenticatedUser = Depends(require_owner)):
        plan_result = planning_service.create_plan(request.query)
        if request.format == "markdown":
            return {
                "format": "markdown",
                "content": plan_result.to_markdown(request=request.query),
                "plan": plan_result.to_metadata(),
            }
        return {"format": "json", "plan": plan_result.to_metadata()}
    # 历史和偏好管理。
    @app.get(
        "/sessions",
        response_model=SessionListResponse,
        summary="分页查询历史会话",
        description="按最近活跃时间倒序返回会话摘要；keyword 对会话 ID 和消息正文做普通包含匹配。date_from / date_to 为可选日期范围（YYYY-MM-DD，date_to 为不包含的上界），按每个会话最近一条消息的时间过滤；不传日期参数时返回全部会话。",
    )
    def list_sessions(
            limit: int = Query(default=20, ge=1, le=100, description="本页最多返回 1-100 条"),
            offset: int = Query(default=0, ge=0, description="从第几条匹配会话开始返回"),
            keyword: str | None = Query(
                default=None,
                max_length=200,
                description="匹配会话 ID 或消息正文；百分号、下划线和反斜杠按普通字符处理",
            ),
            date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="按会话最近一条消息的时间过滤的起始日期（YYYY-MM-DD，包含当天）"),
            date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="按会话最近一条消息的时间过滤的结束日期（YYYY-MM-DD，不包含当天）"),
            user: AuthenticatedUser = Depends(require_user),
    ):
        page = conversation_service.list_sessions(
            user_id=user.id,
            limit=limit,
            offset=offset,
            keyword=keyword,
            date_from=date_from,
            date_to=date_to,
        )
        return {
            **page,
            "items": [item.__dict__ for item in page["items"]],
        }
    @app.get("/sessions/{session_id}")
    def session_detail(
            session_id: str,
            limit: int = Query(default=100, ge=1, le=200),
            user: AuthenticatedUser = Depends(require_user),
    ):
        return conversation_service.get_session_detail(user_id=user.id, session_id=session_id, limit=limit)
    @app.get("/history")
    def history(
            kind: Literal["tasks", "tools"] = Query(default="tasks"),
            limit: int = Query(default=20, ge=1, le=100),
            user: AuthenticatedUser = Depends(require_user),
    ):
        rows = history_service.list_history(kind=kind, user_id=user.id, limit=limit)
        return {"kind": kind, "items": [item.__dict__ for item in rows]}
    @app.get(
        "/runs",
        response_model=RunListResponse,
        summary="分页查询 Agent 运行",
        description="只返回轻量摘要，不包含 Prompt、Trace、完整回答或原始工具参数。",
        responses={503: {"description": "MySQL 运行记录暂时不可用"}},
    )
    def list_agent_runs(
            status: RunStatusFilter | None = Query(default=None, description="按运行状态精确筛选"),
            session_id: str | None = Query(default=None, max_length=128, description="按会话 ID 精确筛选"),
            keyword: str | None = Query(default=None, max_length=200, description="匹配 run_id 或用户输入"),
            limit: int = Query(default=20, ge=1, le=100, description="本页最多返回 1-100 条"),
            offset: int = Query(default=0, ge=0, description="从第几条匹配记录开始返回"),
            user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            return run_service.list_runs(
                user_id=user.id,
                status=status,
                session_id=session_id,
                keyword=keyword,
                limit=limit,
                offset=offset,
            )
        except DailyAgentMemoryError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "run_store_unavailable", "message": str(exc)},
            ) from exc
    register_run_activity_route(app)
    @app.get(
        "/runs/{run_id}",
        response_model=RunDetail,
        summary="查询一次 Agent 运行详情",
        description="返回经过递归脱敏和长度限制的 Prompt、计划、Tool 调用与 Trace。",
        responses={
            404: {"description": "当前 user_id 下不存在该 run_id"},
            422: {"description": "run_id 格式或查询参数不合法"},
            503: {"description": "MySQL 运行记录暂时不可用"},
        },
    )
    def agent_run_detail(
            run_id: str = PathParam(
                min_length=3,
                max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
                description="Agent 运行唯一标识，例如 ask-20260715143200-123456",
            ),
            user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            detail = run_service.get_run(user_id=user.id, run_id=run_id)
        except DailyAgentMemoryError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "run_store_unavailable", "message": str(exc)},
            ) from exc
        if detail is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "run_not_found", "message": "没有找到这次运行，请检查 run_id 或用户身份。"},
            )
        # 运行详情注入 Artifact：先按 user 取得 run，再按 user_id + run_id 查询产物。
        # RunService 不依赖 ArtifactService，旧运行天然得到空数组。
        records, _ = artifact_service.list_records(
            user_id=user.id,
            run_id=detail.run_id,
            limit=100,
            offset=0,
        )
        detail.artifacts = [ArtifactPublic(**record.to_public_dict()) for record in records]
        if not user.is_owner:
            detail = detail.model_copy(update={"prompt_snapshot": None, "model_requests": []})
        return detail
    @app.get(
        "/runs/{run_id}/status",
        summary="查询一次 Agent 运行的工作状态",
        description="优先返回最终快照；运行中或旧记录则从已持久化的 status_updated 事件恢复。",
        responses={
            404: {"description": "当前 user_id 下不存在该 run_id"},
            503: {"description": "MySQL 运行记录暂时不可用"},
        },
    )
    def agent_run_status(
            run_id: str = PathParam(
                min_length=3,
                max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
                description="Agent 运行唯一标识",
            ),
            user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            detail = run_service.get_run(user_id=user.id, run_id=run_id)
            if detail is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "run_not_found", "message": "当前用户下没有找到这次运行。"},
                )
            if detail.working_state:
                return {"run_id": run_id, "state": detail.working_state}
            event_page = run_lifecycle_service.list_events(
                user_id=user.id,
                run_id=run_id,
                after_sequence=0,
                limit=500,
            )
        except DailyAgentMemoryError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "run_status_store_unavailable", "message": str(exc)},
            ) from exc
        states = [
            event.payload.get("state")
            for event in (event_page.events if event_page else [])
            if event.type == "status_updated" and isinstance(event.payload.get("state"), dict)
        ]
        if states:
            return {"run_id": run_id, "state": states[-1]}
        return {
            "run_id": run_id,
            "state": {
                "run_id": run_id,
                "goal_summary": detail.query_summary,
                "phase": _working_phase_from_run_status(detail.status),
                "current_action": detail.stop_reason or "没有可回放的详细状态",
                "completed_steps": [],
                "evidence_summaries": [],
                "active_constraints": [],
                "blocker": detail.error_message,
                "next_action": None,
                "budget": {},
                "updated_at": (detail.finished_at or detail.started_at).isoformat()
                if (detail.finished_at or detail.started_at)
                else None,
            },
        }
    @app.get(
        "/runs/{run_id}/events",
        response_model=RunEventListResponse,
        summary="按 sequence 补拉一次 Agent 运行的事件",
        description="用于页面刷新、短暂断流或检测到 sequence 缺口后补拉事件；事件按 sequence 升序返回。",
        responses={
            404: {"description": "当前 user_id 下不存在该 run_id"},
            422: {"description": "run_id、after_sequence 或 limit 不合法"},
            503: {"description": "MySQL 运行事件暂时不可用"},
        },
    )
    def agent_run_events(
            run_id: str = PathParam(
                min_length=3,
                max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
                description="Agent 运行唯一标识",
            ),
            after_sequence: int = Query(default=0, ge=0, description="只返回 sequence 大于该值的事件"),
            limit: int = Query(default=200, ge=1, le=500, description="本次最多返回 1-500 条事件"),
            user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            payload = run_lifecycle_service.list_events(
                user_id=user.id,
                run_id=run_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        except DailyAgentMemoryError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "run_event_store_unavailable", "message": str(exc)},
            ) from exc
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "run_not_found", "message": "当前用户下没有找到这次运行。"},
            )
        return payload
    @app.post(
        "/runs/{run_id}/cancel",
        response_model=RunCancelResponse,
        summary="停止一次仍在执行的 Agent 运行",
        description="提交后先进入 cancel_requested；执行器确认停止后变为 cancelled。重复请求保持幂等。",
        responses={
            404: {"description": "当前 user_id 下不存在该 run_id"},
            422: {"description": "run_id 或请求体不合法"},
            503: {"description": "MySQL 运行状态暂时不可用"},
        },
    )
    def cancel_agent_run(
            request: RunCancelRequest,
            run_id: str = PathParam(
                min_length=3,
                max_length=128,
                pattern=r"^[A-Za-z0-9._:-]+$",
                description="要停止的 Agent 运行唯一标识",
            ),
            user: AuthenticatedUser = Depends(require_user),
    ):
        payload = run_lifecycle_service.request_cancel(run_id=run_id, user_id=user.id)
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "run_not_found", "message": "当前用户下没有找到这次运行。"},
            )
        return payload
    @app.get("/prefs")
    def list_prefs(user: AuthenticatedUser = Depends(require_user)):
        rows = preference_service.list_preferences(user_id=user.id)
        return {"items": [item.__dict__ for item in rows]}
    @app.post("/prefs")
    def set_prefs(request: PreferenceWriteRequest, user: AuthenticatedUser = Depends(require_user)):
        preference_service.set_preference(request.key, request.value, user_id=user.id)
        return {"status": "success", "key": request.key, "value": request.value, "user_id": user.id}
    @app.get("/skills")
    def list_skills(user: AuthenticatedUser = Depends(require_user)):
        items = skill_catalog_service.list_skills()
        if not user.is_owner:
            items = [sanitize_member_skill_summary(item) for item in items]
        return {"items": items}
    @app.get(
        "/skills/{skill_name}/detail",
        summary="查看 Skill 内部详情",
        description=(
            "返回 Skill 元数据、持久化位置，以及经过路径白名单、长度限制和密钥脱敏后的 "
            "SKILL.md、manifest、scripts、references 与 assets 文件信息。"
        ),
        responses={404: {"description": "Skill 不存在"}},
    )
    def get_skill_detail(skill_name: str, _: AuthenticatedUser = Depends(require_owner)):
        item = skill_catalog_service.get_skill_detail(skill_name)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "skill_not_found", "message": "没有找到这个 Skill。"},
            )
        return item
    @app.post("/skills/import")
    def import_skill(request: SkillImportRequest, _: AuthenticatedUser = Depends(require_owner)):
        items = skill_catalog_service.import_skill(request.path, enable=request.enable, source_url=request.source_url)
        return {"status": "success", "items": items}
    @app.post("/skills/{skill_name}/enable")
    def enable_skill(skill_name: str, _: AuthenticatedUser = Depends(require_owner)):
        item = skill_catalog_service.enable_skill(skill_name)
        return {"status": "success", "item": item}
    @app.post("/skills/{skill_name}/disable")
    def disable_skill(skill_name: str, _: AuthenticatedUser = Depends(require_owner)):
        item = skill_catalog_service.disable_skill(skill_name)
        return {"status": "success", "item": item}
    @app.post("/skills/{skill_name}/adopt")
    def adopt_skill(skill_name: str, _: AuthenticatedUser = Depends(require_owner)):
        item = skill_catalog_service.adopt_skill(skill_name)
        return {"status": "success", "item": item}
    @app.delete("/skills/{skill_name}")
    def delete_skill(skill_name: str, _: AuthenticatedUser = Depends(require_owner)):
        deleted = skill_catalog_service.remove_skill(skill_name)
        return {"status": "success", "deleted": deleted, "name": skill_name}
    register_capability_routes(
        app,
        tool_service=tool_service,
        mcp_service=mcp_service,
        database_service=database_connection_service,
    )
    register_artifact_routes(app, artifact_service=artifact_service)
    register_sharing_routes(app, sharing_service=sharing_service, artifact_service=artifact_service)
    @app.get("/settings/model")
    def get_model_settings(_: AuthenticatedUser = Depends(require_owner)):
        return model_config_service.get_model_config()
    @app.post("/settings/model")
    def save_model_settings(request: ModelConfigWriteRequest, _: AuthenticatedUser = Depends(require_owner)):
        payload = model_config_service.save_model_config(
            provider=request.provider,
            model_id=request.model_id,
            api_key=request.api_key,
            base_url=request.base_url,
            clear_api_key=request.clear_api_key,
            agno_telemetry=request.agno_telemetry,
            thinking_enabled=request.thinking_enabled,
            reasoning_effort=request.reasoning_effort,
            context_token_budget=request.context_token_budget,
        )
        return {"status": "success", **payload}
    @app.post("/settings/model/test", summary="真实测试当前模型配置")
    def test_model_settings(_: AuthenticatedUser = Depends(require_owner)):
        return model_config_service.test_model_connection()
    @app.get("/settings/model/available", summary="获取当前 Provider 可用模型列表")
    def list_available_models(_: AuthenticatedUser = Depends(require_owner)):
        return model_config_service.list_available_models()
    # 报日本身既支持“立即生成”，也支持“保存成定时任务后由 worker 执行”。
    @app.post("/report/generate")
    def generate_report(request: ReportGenerateRequest, user: AuthenticatedUser = Depends(require_owner)):
        repos = [str(resolve_user_workspace_path(user.id, path)) for path in request.repos]
        result = report_service.generate(
            target_date=request.date,
            repos=repos,
            author=request.author,
            extra_notes=request.extra_notes,
        )
        return {"report_text": result.report_text, "trace": result.trace}
    # 定时任务管理接口，供前端工作台直接增删查控。
    @app.get("/schedules")
    def list_schedules(
            limit: int = Query(default=20, ge=1, le=100),
            user: AuthenticatedUser = Depends(require_owner),
    ):
        rows = scheduler_service.list_jobs(user_id=user.id, limit=limit)
        return {"items": [item.__dict__ for item in rows]}
    @app.post("/schedules/report")
    def create_report_schedule(request: ScheduleReportRequest, user: AuthenticatedUser = Depends(require_owner)):
        repos = [str(resolve_user_workspace_path(user.id, path)) for path in request.repos]
        job = scheduler_service.create_daily_report_job(
            title=request.title or f"工作日报 {request.time}",
            schedule_time=request.time,
            repos=repos,
            user_id=user.id,
            timezone_name=request.timezone,
            author=request.author,
            extra_notes=request.extra_notes,
        )
        return {"status": "success", "job": job.__dict__}
    @app.get("/schedules/{job_id}/runs")
    def schedule_runs(
            job_id: int,
            limit: int = Query(default=20, ge=1, le=100),
            user: AuthenticatedUser = Depends(require_owner),
    ):
        scheduler_service.get_job(job_id=job_id, user_id=user.id)
        rows = scheduler_service.list_job_runs(job_id=job_id, limit=limit)
        return {"items": [item.__dict__ for item in rows]}
    @app.post("/schedules/{job_id}/pause")
    def pause_schedule(job_id: int, user: AuthenticatedUser = Depends(require_owner)):
        updated = scheduler_service.set_job_status(job_id, status="paused", user_id=user.id)
        return {"status": "success" if updated else "not_found", "job_id": job_id}
    @app.post("/schedules/{job_id}/resume")
    def resume_schedule(job_id: int, user: AuthenticatedUser = Depends(require_owner)):
        updated = scheduler_service.set_job_status(job_id, status="enabled", user_id=user.id)
        return {"status": "success" if updated else "not_found", "job_id": job_id}
    @app.post("/schedules/{job_id}/run-now")
    def run_schedule_now(job_id: int, user: AuthenticatedUser = Depends(require_owner)):
        result = scheduler_service.run_job_now(job_id=job_id, user_id=user.id)
        return {
            "job": result.job.__dict__,
            "run": result.run.__dict__,
            "report": result.report.report_text if result.report else None,
        }
    # 静态工作台必须最后挂载：根路径 SPA 回退排在所有 API 之后，既支持刷新
    # /login、/register、/agent 等前端路由，也不会吞掉真实 API 的 404。
    mount_workbench_frontends(app, WEB_DIR, NEXT_WEB_DIR)
    return app

try:
    app = create_app()
except Exception as exc:  # pragma: no cover - 避免模块导入阶段被外部依赖直接打断
    logger.warning("API 默认应用初始化失败，将在显式 create_app() 时重试：%s", exc)
    app = FastAPI(title="SB Agent API", version="0.1.0")

def run() -> None:
    """提供一个最小启动入口，便于本地直接起 API 服务。"""
    import uvicorn
    app_context = ApplicationContext.build()
    log_path = setup_logging(process_role="api", paths=app_context.paths)
    logger.info("API 启动：level=%s log_file=%s", log_level_name(), log_path)
    # 在 API 对外监听的同时后台加载模型运行时，避免第一条普通消息承担冷启动等待。
    threading.Thread(target=prewarm_agent_runtime, name="agent-runtime-warmup", daemon=True).start()
    uvicorn.run("daily_agent.api:app", host="127.0.0.1", port=api_port(), reload=False)
