"""登录、邀请和服务端会话的公开数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


UserRole = Literal["owner", "member"]
UserStatus = Literal["active", "disabled"]


@dataclass(frozen=True)
class AuthenticatedUser:
    """服务端已经校验过的当前用户，业务接口只能从这里取得 user_id。"""

    id: str
    email: str
    display_name: str
    role: UserRole
    status: UserStatus

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def to_public_dict(self) -> dict[str, str | bool]:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "status": self.status,
            "is_owner": self.is_owner,
        }


class LoginRequest(BaseModel):
    """邮箱密码登录请求。"""

    email: str = Field(min_length=3, max_length=254, description="受邀注册时使用的邮箱")
    password: str = Field(min_length=1, max_length=256, description="登录密码")


class BootstrapOwnerRequest(BaseModel):
    """系统没有任何用户时创建首位所有者。"""

    email: str = Field(min_length=3, max_length=254, description="所有者登录邮箱")
    display_name: str = Field(min_length=1, max_length=80, description="所有者显示名称")
    password: str = Field(min_length=6, max_length=256, description="至少 6 位的所有者密码")


class InviteCodeCreateRequest(BaseModel):
    """所有者创建可供多人使用的邀请码。"""

    label: str = Field(default="", max_length=80, description="方便管理员识别用途的备注，可留空")
    expires_in_days: int | None = Field(default=7, ge=1, le=3650, description="有效天数；null 表示永久有效")
    max_uses: int | None = Field(default=10, ge=1, le=10000, description="最多注册人数；null 表示不限制")


class InviteCodeRegisterRequest(BaseModel):
    """访客使用批量邀请码注册账号。"""

    code: str = Field(min_length=1, max_length=64, description="管理员提供的邀请码，不区分大小写")
    email: str = Field(min_length=3, max_length=254, description="新账号登录邮箱")
    display_name: str = Field(min_length=1, max_length=80, description="页面显示名称")
    password: str = Field(min_length=6, max_length=256, description="至少 6 位的登录密码")


class UserStatusWriteRequest(BaseModel):
    """所有者启用或禁用成员。"""

    status: UserStatus = Field(description="active 启用，disabled 禁用并撤销全部会话")


class AuthUserResponse(BaseModel):
    """前端可见的用户信息，不包含密码和会话令牌。"""

    id: str
    email: str
    display_name: str
    role: UserRole
    status: UserStatus
    is_owner: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class InviteCodeResponse(BaseModel):
    """仅所有者可见的邀请码信息，包含可再次复制的明文邀请码。"""

    id: str
    code: str
    label: str
    status: Literal["active", "revoked", "expired", "exhausted"]
    use_count: int
    max_uses: int | None
    expires_at: datetime | None
    created_at: datetime
