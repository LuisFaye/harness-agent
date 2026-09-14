"""本轮运行结束时，把用户交付物自动登记为 Artifact。

为什么需要
----------
Agent 经常用 workspace_write_file / shell_execute 把 Word、PDF 写到工作区，
却忘记调用 artifact_register_file。文件在磁盘上，对话页却没有预览和下载入口。

约定
----
- 正文交付物写在 ``outputs/``（递归扫描，跳过隐藏目录与脚本）。
- 工作区根目录仅兜底登记本轮新产生的办公文档/图片，避免把 README.md 当产物。
- 脚本、中间稿不要放进 outputs/；同一次 run 已登记过的 sha256 / source_path 会跳过。
- 登记失败不影响 ask 主流程，只记安全日志（不含绝对路径）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from daily_agent.artifact_models import BINARY_SUFFIXES, SUPPORTED_SUFFIXES
from daily_agent.artifact_service import ArtifactService, ArtifactValidationError


logger = logging.getLogger(__name__)

# 用户交付物目录，相对 Workspace Root。
OUTPUTS_DIRNAME = "outputs"

# 递归扫描时跳过的目录名，避免把依赖和缓存登记成产物。
_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".sb-agent",
    ".daily-agent",
}

# 根目录兜底只收这些扩展名：用户几乎不会把源码保存成这些格式。
_ROOT_FALLBACK_SUFFIXES = BINARY_SUFFIXES

# 给 mtime 一点时钟误差，避免刚写入的文件被误判为「早于本轮」。
_MTIME_SKEW_SECONDS = 2.0


def register_run_deliverables_quietly(
    *,
    user_id: str,
    session_id: str,
    run_id: str,
    started_at_iso: str,
    artifact_service: ArtifactService | None = None,
) -> int:
    """扫描并登记本轮交付物；任何异常都吞掉，返回成功登记条数。"""
    try:
        started_at = _parse_started_at(started_at_iso)
        service = artifact_service or ArtifactService()
        return _register_run_deliverables(
            service,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            started_at=started_at,
        )
    except Exception:
        logger.exception("自动登记本轮交付物失败：run_id=%s", run_id)
        return 0


def _register_run_deliverables(
    service: ArtifactService,
    *,
    user_id: str,
    session_id: str,
    run_id: str,
    started_at: datetime,
) -> int:
    """真正扫描 outputs/ 与工作区根目录，并把新文件登记为 workspace_snapshot。"""
    existing_records, _ = service.list_records(
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        limit=100,
        offset=0,
    )
    seen_sha = {record.sha256 for record in existing_records}
    seen_paths = {
        record.source_path
        for record in existing_records
        if record.source_path
    }
    registered = 0
    for relative in _iter_deliverable_relpaths(service.workspace_root, started_at):
        if relative.as_posix() in seen_paths:
            continue
        try:
            record = service.register_file(
                path=relative.as_posix(),
                summary="本轮自动登记的交付物",
                retained=False,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
            )
        except ArtifactValidationError as exc:
            logger.info(
                "跳过无法登记的交付物：run_id=%s suffix=%s error=%s",
                run_id,
                relative.suffix.lower(),
                type(exc).__name__,
            )
            continue
        except Exception:
            logger.exception("登记交付物失败：run_id=%s", run_id)
            continue
        if record.sha256 in seen_sha:
            # 内容与本轮已有产物相同（模型先手工登记过），删掉刚产生的重复快照。
            try:
                service.delete(record.artifact_id, user_id=user_id, session_id=session_id, run_id=run_id)
            except Exception:
                logger.info("清理重复交付物失败：run_id=%s", run_id)
            continue
        seen_sha.add(record.sha256)
        seen_paths.add(relative.as_posix())
        registered += 1
    if registered:
        logger.info("本轮自动登记交付物 %s 个：run_id=%s", registered, run_id)
    return registered


def _iter_deliverable_relpaths(workspace_root: Path, started_at: datetime) -> list[Path]:
    """返回本轮应登记文件相对工作区的路径，outputs/ 优先、根目录办公文件兜底。"""
    started_ts = started_at.timestamp()
    found: list[Path] = []
    seen: set[str] = set()
    outputs = workspace_root / OUTPUTS_DIRNAME
    if outputs.is_dir() and not outputs.is_symlink():
        for path in outputs.rglob("*"):
            rel = _as_deliverable(workspace_root, path, started_ts, allowed=SUPPORTED_SUFFIXES)
            if rel is None or rel.as_posix() in seen:
                continue
            seen.add(rel.as_posix())
            found.append(rel)
    try:
        children = list(workspace_root.iterdir())
    except OSError:
        children = []
    for path in children:
        rel = _as_deliverable(workspace_root, path, started_ts, allowed=_ROOT_FALLBACK_SUFFIXES)
        if rel is None or rel.as_posix() in seen:
            continue
        seen.add(rel.as_posix())
        found.append(rel)
    found.sort(key=lambda item: item.as_posix())
    return found


def _as_deliverable(
    workspace_root: Path,
    path: Path,
    started_ts: float,
    *,
    allowed: tuple[str, ...],
) -> Path | None:
    """单个路径若是本轮新产生的普通交付文件，返回相对路径，否则返回 None。"""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.name.startswith("."):
            return None
        suffix = path.suffix.lower()
        if suffix not in allowed:
            return None
        relative = path.resolve().relative_to(workspace_root.resolve())
        if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in relative.parts[:-1]):
            return None
        if path.stat().st_mtime + _MTIME_SKEW_SECONDS < started_ts:
            return None
        return relative
    except (OSError, ValueError):
        return None


def _parse_started_at(started_at_iso: str) -> datetime:
    """把运行开始时间解析成带时区的 datetime，解析失败则退回当前时间。"""
    text = str(started_at_iso or "").strip()
    if not text:
        return datetime.now().astimezone()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now().astimezone()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc).astimezone()
    return parsed
