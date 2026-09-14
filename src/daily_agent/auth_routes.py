"""登录、初始化、邀请注册和成员管理 HTTP 接口。"""

from __future__ import annotations

import os
import logging
from fastapi import Depends, FastAPI, HTTPException, Request, Response

from daily_agent.auth_dependencies import get_auth_service, require_owner, require_user
from daily_agent.auth_models import (
    AuthenticatedUser,
    BootstrapOwnerRequest,
    InviteCodeCreateRequest,
    InviteCodeRegisterRequest,
    LoginRequest,
    UserStatusWriteRequest,
)
from daily_agent.auth_service import AuthError, AuthService, SESSION_COOKIE_NAME, SESSION_TTL_DAYS


logger = logging.getLogger(__name__)


def register_auth_routes(
    app: FastAPI,
    *,
    auth_service: AuthService | None = None,
    legacy_user_claimers: list | None = None,
) -> None:
    """注册公开认证入口和仅所有者可访问的成员管理接口。"""
    service = auth_service or get_auth_service()

    @app.get("/auth/setup-status", summary="检查是否需要初始化首位所有者")
    def setup_status():
        return {"setup_required": service.setup_required()}

    @app.post("/auth/bootstrap", summary="创建系统首位所有者")
    def bootstrap_owner(payload: BootstrapOwnerRequest, request: Request, response: Response):
        try:
            user = service.bootstrap_owner(
                email=payload.email,
                password=payload.password,
                display_name=payload.display_name,
            )
            for claim in legacy_user_claimers or []:
                try:
                    claim("default", user.id)
                except Exception:
                    logger.exception("首位所有者接管历史文件失败：claimer=%s", getattr(claim, "__name__", "unknown"))
            _, token, _ = service.login(email=payload.email, password=payload.password)
        except AuthError as exc:
            raise _http_error(exc) from exc
        _set_session_cookie(response, token, request)
        return {"user": user.to_public_dict()}

    @app.post("/auth/login", summary="使用邮箱密码登录")
    def login(payload: LoginRequest, request: Request, response: Response):
        try:
            user, token, _ = service.login(email=payload.email, password=payload.password)
        except AuthError as exc:
            raise _http_error(exc) from exc
        _set_session_cookie(response, token, request)
        response.headers["Cache-Control"] = "no-store"
        return {"user": user.to_public_dict()}

    @app.post("/auth/logout", summary="退出当前登录")
    def logout(
        request: Request,
        response: Response,
        user: AuthenticatedUser = Depends(require_user),
    ):
        service.logout(request.cookies.get(SESSION_COOKIE_NAME), actor_user_id=user.id)
        response.delete_cookie(SESSION_COOKIE_NAME, path="/", httponly=True, samesite="lax")
        response.headers["Cache-Control"] = "no-store"
        return {"status": "success"}

    @app.get("/auth/me", summary="读取当前登录用户")
    def me(user: AuthenticatedUser = Depends(require_user)):
        return {"user": user.to_public_dict()}

    @app.post(
        "/auth/register",
        summary="使用邀请码注册并登录",
        description="邀请码不绑定邮箱；成功注册会立即启用账号并建立当前浏览器登录会话。",
    )
    def register(payload: InviteCodeRegisterRequest, request: Request, response: Response):
        try:
            user = service.register_with_invite_code(
                code=payload.code,
                email=payload.email,
                password=payload.password,
                display_name=payload.display_name,
            )
            _, token, _ = service.login(email=payload.email, password=payload.password)
        except AuthError as exc:
            raise _http_error(exc) from exc
        _set_session_cookie(response, token, request)
        response.headers["Cache-Control"] = "no-store"
        return {"status": "success", "user": user.to_public_dict()}

    @app.get("/auth/users", summary="查看成员列表")
    def list_users(_: AuthenticatedUser = Depends(require_owner)):
        return {"items": service.list_users()}

    @app.patch("/auth/users/{user_id}/status", summary="启用或禁用成员")
    def set_user_status(
        user_id: str,
        payload: UserStatusWriteRequest,
        owner: AuthenticatedUser = Depends(require_owner),
    ):
        try:
            user = service.set_user_status(user_id, status=payload.status, actor_user_id=owner.id)
        except AuthError as exc:
            raise _http_error(exc) from exc
        return {"user": user}

    @app.post(
        "/auth/users/{user_id}/password-reset",
        summary="重置成员密码",
        description="仅所有者可调用；生成随机强密码、撤销该成员全部登录会话，并仅在本次响应中返回明文新密码。",
    )
    def reset_user_password(
        user_id: str,
        owner: AuthenticatedUser = Depends(require_owner),
    ):
        try:
            new_password = service.reset_user_password(user_id, actor_user_id=owner.id)
        except AuthError as exc:
            raise _http_error(exc) from exc
        return {"status": "success", "password": new_password}

    @app.get(
        "/auth/invite-codes",
        summary="查看邀请码",
        description="仅所有者可访问；按产品约定返回可再次复制的明文邀请码及使用情况。",
    )
    def list_invite_codes(_: AuthenticatedUser = Depends(require_owner)):
        return {"items": service.list_invite_codes()}

    @app.post(
        "/auth/invite-codes",
        summary="创建批量邀请码",
        description="有效期和最大使用次数都可传 null，分别表示永久有效和不限人数。",
    )
    def create_invite_code(
        payload: InviteCodeCreateRequest,
        owner: AuthenticatedUser = Depends(require_owner),
    ):
        try:
            invite_code = service.create_invite_code(
                label=payload.label,
                created_by=owner.id,
                expires_in_days=payload.expires_in_days,
                max_uses=payload.max_uses,
            )
        except AuthError as exc:
            raise _http_error(exc) from exc
        return {"invite_code": invite_code}

    @app.delete("/auth/invite-codes/{invite_code_id}", summary="停用邀请码")
    def revoke_invite_code(
        invite_code_id: str,
        owner: AuthenticatedUser = Depends(require_owner),
    ):
        changed = service.revoke_invite_code(invite_code_id, actor_user_id=owner.id)
        if not changed:
            raise HTTPException(
                status_code=404,
                detail={"code": "invite_code_not_found", "message": "邀请码不存在或已经停用。"},
            )
        return {"status": "success", "invite_code_id": invite_code_id}


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    secure_override = os.getenv("SB_AGENT_COOKIE_SECURE")
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    secure = (
        secure_override.strip().lower() in {"1", "true", "yes", "on"}
        if secure_override is not None
        else request.url.scheme == "https" or forwarded_proto == "https"
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _public_origin(request: Request) -> str:
    configured = os.getenv("SB_AGENT_PUBLIC_ORIGIN", "").strip().rstrip("/")
    if configured:
        return configured
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost")).split(",", 1)[0].strip()
    return f"{proto}://{host}"


def _http_error(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})
