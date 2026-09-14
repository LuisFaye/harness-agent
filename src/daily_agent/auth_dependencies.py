"""FastAPI 认证依赖，集中提供当前用户和所有者权限校验。"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException, Request

from daily_agent.auth_models import AuthenticatedUser
from daily_agent.auth_service import AuthService, SESSION_COOKIE_NAME


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return AuthService()


def require_user(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthenticatedUser:
    """从 HttpOnly Cookie 恢复当前用户；业务接口不得再信任客户端 user_id。"""
    user = auth_service.authenticate(request.cookies.get(SESSION_COOKIE_NAME))
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "authentication_required", "message": "请先登录后再继续。"},
            headers={"Cache-Control": "no-store"},
        )
    return user


def require_owner(user: AuthenticatedUser = Depends(require_user)) -> AuthenticatedUser:
    if not user.is_owner:
        raise HTTPException(
            status_code=403,
            detail={"code": "owner_required", "message": "只有系统所有者可以执行这个操作。"},
        )
    return user
