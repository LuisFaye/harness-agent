"""提供可核对的服务启动时间、包版本和 Git 提交信息。"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


SERVICE_STARTED_AT = datetime.now().astimezone().isoformat()


def build_runtime_metadata(workspace_root: Path) -> dict[str, str | None]:
    configured_commit = str(os.getenv("SB_AGENT_COMMIT_SHA") or "").strip()
    commit = configured_commit or _read_git_commit(workspace_root)
    try:
        package_version = version("sb-agent")
    except PackageNotFoundError:
        package_version = "development"
    return {
        "version": package_version,
        "commit": commit,
        "started_at": SERVICE_STARTED_AT,
    }


def _read_git_commit(workspace_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None
