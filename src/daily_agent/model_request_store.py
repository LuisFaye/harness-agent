"""模型请求体的本地短命存档。

产品约定：完整 OpenAI 兼容请求只偶发查看、不进 MySQL。
落盘到 ``data_dir/model-requests/{run_id}.json``，保留最近 7 天且最多 50 份，
超额或过期删最旧。读写失败只记日志，不影响主任务。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from daily_agent.paths import AppPaths

logger = logging.getLogger(__name__)

_MAX_AGE = timedelta(days=7)
_MAX_FILES = 50
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_lock = threading.Lock()


def save_model_requests(
    run_id: str,
    requests: list[dict[str, Any]],
    *,
    user_id: str | None = None,
    root: Path | None = None,
) -> bool:
    """把本轮截获的请求列表写成本地 JSON；空列表不写文件。

    返回是否成功落盘。调用方应在成功后把 MySQL metadata 里的全文去掉。
    """
    cleaned_run_id = _normalize_run_id(run_id)
    if not cleaned_run_id or not requests:
        return False
    directory = _store_dir(root)
    path = directory / f"{cleaned_run_id}.json"
    payload = {
        "run_id": cleaned_run_id,
        "user_id": user_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "request_count": len(requests),
        "model_requests": list(requests),
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
        _cleanup_store(directory)
        return True
    except OSError:
        logger.exception("写入模型请求本地存档失败：run_id=%s", cleaned_run_id)
        return False


def load_model_requests(run_id: str, *, root: Path | None = None) -> list[dict[str, Any]]:
    """按 run_id 读取本地存档；不存在或损坏时返回空列表。"""
    cleaned_run_id = _normalize_run_id(run_id)
    if not cleaned_run_id:
        return []
    path = _store_dir(root) / f"{cleaned_run_id}.json"
    if not path.is_file():
        return []
    try:
        # 过期文件按需清理，避免详情页还能打开很久以前的大 JSON。
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if age > _MAX_AGE:
            path.unlink(missing_ok=True)
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        logger.exception("读取模型请求本地存档失败：run_id=%s", cleaned_run_id)
        return []
    items = payload.get("model_requests") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _store_dir(root: Path | None) -> Path:
    base = root if root is not None else AppPaths.discover().data_dir
    return (base / "model-requests").resolve()


def _normalize_run_id(run_id: str) -> str | None:
    text = str(run_id or "").strip()
    if not text or not _SAFE_RUN_ID.fullmatch(text):
        return None
    # 文件名里再去掉路径分隔风格字符，虽然正则已排除 /。
    return text.replace("/", "_").replace("\\", "_")


def _cleanup_store(directory: Path) -> None:
    """按 mtime 删除过期文件，并在超过上限时删最旧的。"""
    with _lock:
        try:
            files = [path for path in directory.glob("*.json") if path.is_file()]
        except OSError:
            logger.exception("扫描模型请求存档目录失败")
            return
        now = datetime.now(timezone.utc)
        survivors: list[tuple[float, Path]] = []
        for path in files:
            try:
                mtime = path.stat().st_mtime
                age = now - datetime.fromtimestamp(mtime, tz=timezone.utc)
                if age > _MAX_AGE:
                    path.unlink(missing_ok=True)
                    continue
                survivors.append((mtime, path))
            except OSError:
                continue
        survivors.sort(key=lambda item: item[0])  # 旧 → 新
        overflow = len(survivors) - _MAX_FILES
        for _, path in survivors[: max(0, overflow)]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("删除超额模型请求存档失败：%s", path)
