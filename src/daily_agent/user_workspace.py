"""按登录用户分配独立工作区，避免多人共享宿主机目录。"""

from __future__ import annotations

import re
from pathlib import Path

from daily_agent.paths import AppPaths


_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def user_workspace_root(user_id: str, *, paths: AppPaths | None = None) -> Path:
    """返回当前用户的私有工作区；CLI 的 default 身份继续保留旧工作区兼容。"""
    normalized = str(user_id or "").strip()
    resolved = paths or AppPaths.discover()
    if normalized == "default":
        return resolved.workspace_root
    if not _USER_ID_RE.fullmatch(normalized):
        raise ValueError("用户 ID 不合法，无法分配工作区。")
    root = (resolved.data_dir / "users" / normalized / "workspace").resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def resolve_user_workspace_path(user_id: str, raw_path: str) -> Path:
    """解析用户提供的目录路径，并确保真实路径仍位于该用户工作区内。"""
    root = user_workspace_root(user_id)
    candidate = Path(str(raw_path or "").strip())
    if not str(candidate) or ".." in candidate.parts:
        raise ValueError("工作目录不能为空，也不能包含 ..。")
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("工作目录超出当前用户的私人工作空间。") from exc
    if not resolved.is_dir():
        raise ValueError("工作目录不存在或不是目录。")
    return resolved
