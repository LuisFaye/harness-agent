"""多人模式下普通成员可见数据的脱敏规则。"""

from __future__ import annotations

from typing import Any


def sanitize_member_skill_summary(item: dict[str, Any]) -> dict[str, Any]:
    """保留朋友对接所需信息，同时移除本机路径、诊断和执行实现细节。"""
    sanitized = dict(item)
    sanitized["path"] = ""
    sanitized["origin_path"] = None
    sanitized["install_source"] = None
    sanitized["diagnostics"] = []
    sanitized.pop("execution", None)
    return sanitized
