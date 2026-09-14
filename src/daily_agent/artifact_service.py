"""Artifact 本地存储与数据契约服务。

职责
----
- 默认根目录使用 ``AppPaths.discover().data_dir / "artifacts"``，不硬编码用户 HOME；
- 文件快照与 JSON 元数据分目录保存：``files/`` 存放内容，``records/`` 存放元数据；
- 元数据与内容都采用「同目录临时文件 + fsync + os.replace」原子发布，避免半写；
- 并发读写由进程内锁保护；目录尽可能 0700、文件尽可能 0600（平台不支持 chmod 不阻断）；
- 创建文本最大 2MB、登记工作区文件最大 10MB、空文件拒绝；
- 文本产物支持 Markdown/纯文本/CSV/JSON，`.json` 必须能 json.loads，文本必须能安全解码
  （UTF-8/UTF-8 BOM/GB18030），含 NUL 的二进制内容拒绝；办公文档与图片按魔数校验后登记；
- 工作区登记复用 Workspace 安全解析：根目录内、拒绝 ``..``、敏感路径、越界、符号链接
  与非普通文件；以受控分块复制 + SHA-256 形成不可变快照；
- 默认 ``retained=false``、``expires_at=created_at+30 天``；``retained=true`` 时过期时间为
  null；取消保留时从当前时间重新计算 30 天；
- 每次 list/get/create/register/retain/delete 做轻量惰性清理：只删除已过期且未保留的
  记录与对应文件，异常或不确定记录跳过并记录安全日志（只含 artifact_id，不含路径）；
- delete 是同一 user 所有权校验后的硬删除；文件删除失败时保留记录可重试，文件本身
  不存在才清理元数据；跨 user/session/run 访问一律返回「产物不存在或不属于当前范围」。
- 磁盘私有元数据一律按不可信输入校验：artifact_id 必须与记录文件名一致，storage_path
  必须严格等于 files_dir + namespace + artifact_id + suffix 重算出的确定路径，异常或
  损坏记录跳过并记录不含绝对路径的安全日志，绝不因坏记录误删文件或阻断其他操作。
- register_file 对源文件只做一次受控打开（平台支持时加 O_NOFOLLOW），fstat 确认普通
  文件后在同一 fd 上有界分块读取、校验并计算 SHA-256，同一份已验证字节发布为产物，
  杜绝“校验一份、复制另一份”的 TOCTOU 风险。
- 下载复用同一套受控 fd 语义：在服务锁内复用 get 的归属校验，只打开严格元数据校验
  重算出的确定 storage_path，O_NOFOLLOW + fstat 确认普通文件后在同一 fd 上有界分块
  读取，实际字节数必须等于记录 size、SHA-256 必须等于记录 sha256，返回已验证的内存
  字节快照，消除“先检查路径、响应对象再按路径打开”的检查与发送分离竞态。

公开边界
--------
任何公开模型、API、Tool 输出、日志都不得包含 ``storage_path``、Artifact 根路径或主机
绝对路径；``storage_path`` 只存在于磁盘上的私有元数据 JSON 中，由 ``to_public_dict``
负责剥离。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from daily_agent.artifact_record import ArtifactRecord
from daily_agent.artifact_models import SUPPORTED_SUFFIXES, TEXT_SUFFIXES
from daily_agent.artifact_validate import (
    ArtifactValidationError,
    validate_artifact_bytes as _validate_artifact_bytes,
    validate_text_bytes as _validate_text_bytes,
)
from daily_agent.paths import AppPaths
from daily_agent.workspace_tools import _is_sensitive_path, _is_within


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 契约常量

# 创建文本与登记工作区文件的字节上限。
MAX_CREATE_BYTES = 2 * 1024 * 1024
MAX_REGISTER_BYTES = 10 * 1024 * 1024

# 文件名长度上限与摘要长度上限。
MAX_FILENAME_LENGTH = 180
MAX_SUMMARY_CHARS = 500

# 归属字段（user/session/run）长度上限，与磁盘记录校验完全一致。
MAX_OWNER_LENGTH = 256

# workspace_snapshot.source_path 的合理长度上限。
MAX_SOURCE_PATH_CHARS = 1024

# 未保留产物的默认生命周期（天）。
RETENTION_DAYS = 30

# 受控分块复制/哈希的块大小。
COPY_CHUNK_BYTES = 64 * 1024

# 文件/目录权限；平台不支持 chmod 时不阻断写入。
FILE_MODE = 0o600
DIR_MODE = 0o700

# 文件名中需要清除的控制字符：NUL 与 C0 控制字符、DEL。
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

# 各扩展名对应的 MIME 类型。
_CONTENT_TYPES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".svg": "image/svg+xml",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# artifact_id 必须是 32 位小写十六进制；sha256 必须是 64 位小写十六进制。
_ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactNotFoundError(ValueError):
    """所有权不匹配或记录不存在时抛出，映射为 404。"""


class ArtifactService:
    """Artifact 的存储、查询、保留与删除服务。"""

    def __init__(
        self,
        root: Path | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        """初始化服务。

        Args:
            root: Artifact 根目录；默认 ``AppPaths.discover().data_dir / "artifacts"``。
            workspace_root: 登记工作区文件使用的 Workspace Root；默认取 AppPaths。
        """
        paths = AppPaths.discover()
        self.root = (root or paths.data_dir / "artifacts").resolve()
        self.workspace_root = (workspace_root or paths.workspace_root).resolve()
        # 文件内容与元数据分目录保存。
        self.files_dir = self.root / "files"
        self.records_dir = self.root / "records"
        # 目录在首次写入时创建，避免只读探针或单元测试产生副作用。
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 公开操作

    def create_text(
        self,
        *,
        filename: str,
        content: str | bytes,
        summary: str | None = None,
        retained: bool = False,
        user_id: str,
        session_id: str,
        run_id: str,
    ) -> ArtifactRecord:
        """把 Agent 生成的文本创建为 Artifact。

        校验文件名、编码、大小与 JSON 可解析性后，先原子写入内容文件，
        再原子发布元数据记录。
        """
        user_id = _normalize_owner(user_id, label="user_id")
        session_id = _normalize_owner(session_id, label="session_id")
        run_id = _normalize_owner(run_id, label="run_id")
        safe_name = _sanitize_filename(filename)
        suffix = Path(safe_name).suffix.lower()
        # create_text 只允许文本扩展名，避免用纯文本冒充 docx/pdf 交付物。
        if suffix not in TEXT_SUFFIXES:
            raise ArtifactValidationError(
                "artifact_create_text 只支持 "
                + "、".join(TEXT_SUFFIXES)
                + "；办公文档与图片请写入 outputs/ 后再登记。"
            )
        if isinstance(content, str):
            data = content.encode("utf-8")
        elif isinstance(content, bytes):
            data = content
        else:
            raise ArtifactValidationError("content 必须是字符串或字节。")
        _validate_text_bytes(data, suffix=suffix, filename=safe_name, max_bytes=MAX_CREATE_BYTES)
        clean_summary = _clean_summary(summary)
        artifact_id = uuid.uuid4().hex
        storage_path = self._storage_path(user_id, session_id, run_id, artifact_id, suffix)
        with self._lock:
            self._lazy_cleanup()
            try:
                _atomic_write(storage_path, data)
            except OSError as exc:
                raise ArtifactValidationError(
                    f"写入产物文件失败（{_safe_error_summary(exc)}）。"
                ) from exc
            record = ArtifactRecord(
                artifact_id=artifact_id,
                user_id=_owner_text(user_id),
                session_id=_owner_text(session_id),
                run_id=_owner_text(run_id),
                filename=safe_name,
                suffix=suffix,
                content_type=_content_type_for(suffix),
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                summary=clean_summary,
                source_kind="generated_text",
                source_path=None,
                created_at=_now_iso(),
                retained=bool(retained),
                expires_at=None if retained else _expires_at_iso(),
                storage_path=str(storage_path),
            )
            try:
                self._write_record(record)
            except OSError as exc:
                # 元数据发布失败时尽力回滚内容文件，避免留下孤儿文件。
                try:
                    storage_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ArtifactValidationError(
                    f"发布产物元数据失败（{_safe_error_summary(exc)}）。"
                ) from exc
            return record

    def register_file(
        self,
        *,
        path: str,
        filename: str | None = None,
        summary: str | None = None,
        retained: bool = False,
        user_id: str,
        session_id: str,
        run_id: str,
    ) -> ArtifactRecord:
        """把安全工作区文件复制为不可变快照并登记。

        - 复用 ``_resolve_safe_path``：根目录内、拒绝 ``..``、敏感路径与越界；
        - 额外拒绝符号链接（含路径链上的任何链接）与非普通文件；
        - 对源文件只做一次受控打开（平台支持时加 O_NOFOLLOW），fstat 确认普通文件
          与大小后在同一 fd 上有界分块读取、校验并计算 SHA-256，同一份已验证字节
          发布为 Artifact；登记后源文件变化不影响 Artifact 内容，也杜绝两次读取
          之间源文件被替换的 TOCTOU 绕过。
        """
        user_id = _normalize_owner(user_id, label="user_id")
        session_id = _normalize_owner(session_id, label="session_id")
        run_id = _normalize_owner(run_id, label="run_id")
        root, resolved, relative = self._resolve_workspace_path(str(path or ""))
        candidate = Path(str(path or "").strip())
        absolute = candidate if candidate.is_absolute() else root / candidate
        # resolve() 会解开符号链接；原始路径与解析结果不一致说明存在链接，一律拒绝。
        if absolute.is_symlink() or resolved.is_symlink() or resolved != absolute:
            raise ArtifactValidationError("不支持符号链接，请登记工作区内的真实文件。")
        try:
            pre_stat = resolved.stat()
        except OSError as exc:
            raise ArtifactValidationError(
                f"无法访问待登记文件（{_safe_error_summary(exc)}）。"
            ) from exc
        if not stat.S_ISREG(pre_stat.st_mode):
            raise ArtifactValidationError("只支持登记普通文件（目录、管道、设备等已拒绝）。")
        if pre_stat.st_size <= 0:
            raise ArtifactValidationError("空文件不能登记为产物。")
        if pre_stat.st_size > MAX_REGISTER_BYTES:
            raise ArtifactValidationError(
                f"文件超过 {MAX_REGISTER_BYTES // 1024 // 1024}MB 登记上限。"
            )
        safe_name = _sanitize_filename(filename or resolved.name)
        suffix = Path(safe_name).suffix.lower()
        # 一次受控打开得到同一个 fd：O_NOFOLLOW 拒绝打开瞬间被替换成符号链接，
        # fstat 再次确认普通文件与大小，再在同一 fd 上有界分块读取并计算 SHA-256。
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(resolved), flags)
        except OSError as exc:
            raise ArtifactValidationError(
                f"无法打开待登记文件（{_safe_error_summary(exc)}）。"
            ) from exc
        try:
            fd_stat = os.fstat(fd)
            if not stat.S_ISREG(fd_stat.st_mode):
                raise ArtifactValidationError("只支持登记普通文件（目录、管道、设备等已拒绝）。")
            if fd_stat.st_size <= 0:
                raise ArtifactValidationError("空文件不能登记为产物。")
            if fd_stat.st_size > MAX_REGISTER_BYTES:
                raise ArtifactValidationError(
                    f"文件超过 {MAX_REGISTER_BYTES // 1024 // 1024}MB 登记上限。"
                )
            # 打开后校验仍是解析时看到的同一个文件，防止打开前被整体替换。
            if (fd_stat.st_dev, fd_stat.st_ino) != (pre_stat.st_dev, pre_stat.st_ino):
                raise ArtifactValidationError("待登记文件在读取期间发生变化，请重试。")
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(fd, COPY_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                # 即使 fstat 时未超限，读取过程中文件也可能被追加，必须持续限制上限。
                if total > MAX_REGISTER_BYTES:
                    raise ArtifactValidationError(
                        f"文件超过 {MAX_REGISTER_BYTES // 1024 // 1024}MB 登记上限。"
                    )
                digest.update(chunk)
                chunks.append(chunk)
            data = b"".join(chunks)
        finally:
            os.close(fd)
        # 文本走解码/JSON 校验；办公文档与图片只核魔数，避免把 docx 当文本拒绝。
        _validate_artifact_bytes(data, suffix=suffix, filename=safe_name, max_bytes=MAX_REGISTER_BYTES)
        clean_summary = _clean_summary(summary)
        artifact_id = uuid.uuid4().hex
        storage_path = self._storage_path(user_id, session_id, run_id, artifact_id, suffix)
        with self._lock:
            self._lazy_cleanup()
            try:
                _atomic_write(storage_path, data)
            except OSError as exc:
                raise ArtifactValidationError(
                    f"登记产物文件失败（{_safe_error_summary(exc)}）。"
                ) from exc
            record = ArtifactRecord(
                artifact_id=artifact_id,
                user_id=_owner_text(user_id),
                session_id=_owner_text(session_id),
                run_id=_owner_text(run_id),
                filename=safe_name,
                suffix=suffix,
                content_type=_content_type_for(suffix),
                size=len(data),
                sha256=digest.hexdigest(),
                summary=clean_summary,
                source_kind="workspace_snapshot",
                source_path=relative.as_posix(),
                created_at=_now_iso(),
                retained=bool(retained),
                expires_at=None if retained else _expires_at_iso(),
                storage_path=str(storage_path),
            )
            try:
                self._write_record(record)
            except OSError as exc:
                try:
                    storage_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ArtifactValidationError(
                    f"发布产物元数据失败（{_safe_error_summary(exc)}）。"
                ) from exc
            return record

    def claim_legacy_user(self, old_user_id: str, new_user_id: str) -> int:
        """首位所有者接管历史 Artifact，并按新归属重算受控存储路径。"""
        changed = 0
        with self._lock:
            if not self.records_dir.is_dir():
                return 0
            for record_path in self.records_dir.glob("*.json"):
                record = self._read_record_path(record_path)
                if record is None or record.user_id != old_user_id:
                    continue
                source = Path(record.storage_path)
                if not source.is_file():
                    continue
                target = self._storage_path(
                    new_user_id,
                    record.session_id,
                    record.run_id,
                    record.artifact_id,
                    record.suffix,
                )
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source.replace(target)
                    migrated = replace(record, user_id=new_user_id, storage_path=str(target))
                    self._write_record(migrated)
                except OSError:
                    logger.exception("迁移历史 Artifact 失败：artifact_id=%s", record.artifact_id)
                    continue
                changed += 1
        return changed

    def list_records(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
        run_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[ArtifactRecord], int]:
        """按 user + 可选 session/run 分页查询，创建时间倒序。

        user_id 必须匹配；session_id/run_id 传 None 表示不限制该维度。
        limit/offset 非整数统一转成精简校验错误，不让 ValueError 逃逸到 API/Tool。
        """
        try:
            safe_limit = max(1, min(100, int(limit)))
            safe_offset = max(0, int(offset))
        except (TypeError, ValueError) as exc:
            raise ArtifactValidationError("limit 和 offset 必须是整数。") from exc
        with self._lock:
            self._lazy_cleanup()
            matches: list[ArtifactRecord] = []
            for record_path in self.records_dir.glob("*.json"):
                record = self._read_record_path(record_path)
                if record is None:
                    continue
                if record.user_id != _owner_text(user_id):
                    continue
                if session_id is not None and record.session_id != _owner_text(session_id):
                    continue
                if run_id is not None and record.run_id != _owner_text(run_id):
                    continue
                matches.append(record)
            matches.sort(key=lambda item: _parse_dt(item.created_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            total = len(matches)
            page = matches[safe_offset:safe_offset + safe_limit]
            return page, total

    def get(
        self,
        artifact_id: str,
        *,
        user_id: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> ArtifactRecord:
        """按 ID 读取一条记录；user/session/run 任一不匹配都返回「不存在」。"""
        with self._lock:
            self._lazy_cleanup()
            record = self._read_record_path(self._record_path(artifact_id))
            if record is None or record.user_id != _owner_text(user_id):
                raise ArtifactNotFoundError("产物不存在或不属于当前范围。")
            if session_id is not None and record.session_id != _owner_text(session_id):
                raise ArtifactNotFoundError("产物不存在或不属于当前范围。")
            if run_id is not None and record.run_id != _owner_text(run_id):
                raise ArtifactNotFoundError("产物不存在或不属于当前范围。")
            return record

    def download_content(
        self,
        artifact_id: str,
        *,
        user_id: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[bytes, ArtifactRecord]:
        """读取并完整校验内容字节快照，返回 (已验证字节, 公开记录)。

        所有权语义与 get 完全一致：复用 get 完成元数据读取与内容快照，整个过程在
        同一服务锁内。只打开已经由严格元数据校验重算出的确定 storage_path，
        O_NOFOLLOW（平台支持时）+ fstat 确认普通文件后，在同一 fd 上有界分块读取；
        实际字节数必须等于记录 size、SHA-256 必须等于记录 sha256 且不超过登记上限。
        符号链接、非普通文件、大小/哈希不一致或文件缺失统一视为内容不可用，公开
        信息只使用安全摘要，绝不包含绝对路径。
        """
        with self._lock:
            record = self.get(
                artifact_id,
                user_id=user_id,
                session_id=session_id,
                run_id=run_id,
            )
            return self._read_verified_bytes(record), record

    def set_retained(
        self,
        artifact_id: str,
        *,
        retained: bool,
        user_id: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> ArtifactRecord:
        """切换保留状态：保留时 expires_at 置空；取消保留时从当前时间重算 30 天。

        user/session/run 语义与 get 一致：session_id/run_id 传 None 表示不限制该维度，
        非 None 时必须精确匹配，否则统一返回「不存在」。
        """
        with self._lock:
            self._lazy_cleanup()
            record = self._read_record_path(self._record_path(artifact_id))
            if record is None or record.user_id != _owner_text(user_id):
                raise ArtifactNotFoundError("产物不存在或不属于当前范围。")
            if session_id is not None and record.session_id != _owner_text(session_id):
                raise ArtifactNotFoundError("产物不存在或不属于当前范围。")
            if run_id is not None and record.run_id != _owner_text(run_id):
                raise ArtifactNotFoundError("产物不存在或不属于当前范围。")
            updated = ArtifactRecord(
                artifact_id=record.artifact_id,
                user_id=record.user_id,
                session_id=record.session_id,
                run_id=record.run_id,
                filename=record.filename,
                suffix=record.suffix,
                content_type=record.content_type,
                size=record.size,
                sha256=record.sha256,
                summary=record.summary,
                source_kind=record.source_kind,
                source_path=record.source_path,
                created_at=record.created_at,
                retained=bool(retained),
                expires_at=None if retained else _expires_at_iso(),
                storage_path=record.storage_path,
            )
            try:
                self._write_record(updated)
            except OSError as exc:
                raise ArtifactValidationError(
                    f"更新产物保留状态失败（{_safe_error_summary(exc)}）。"
                ) from exc
            return updated

    def delete(
        self,
        artifact_id: str,
        *,
        user_id: str,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """同一 user 所有权校验后的硬删除（可选 session/run 闭包校验）。

        文件删除失败时不删除元数据、不谎称完成，保留记录可重试；文件本来不存在时
        清理元数据并返回 deleted=true；记录不存在或越权返回「不存在」。
        """
        with self._lock:
            self._lazy_cleanup()
            record = self._read_record_path(self._record_path(artifact_id))
            if record is None or record.user_id != _owner_text(user_id):
                raise ArtifactNotFoundError("产物不存在或不属于当前范围。")
            if session_id is not None and record.session_id != _owner_text(session_id):
                raise ArtifactNotFoundError("产物不存在或不属于当前范围。")
            if run_id is not None and record.run_id != _owner_text(run_id):
                raise ArtifactNotFoundError("产物不存在或不属于当前范围。")
            file_path = Path(record.storage_path)
            try:
                file_path.unlink()
            except FileNotFoundError:
                # 内容文件本来不存在：记录已无法下载，允许继续清理元数据。
                pass
            except OSError as exc:
                # 文件删除失败必须保留记录可重试，安全返回失败而不是假装完成。
                logger.warning(
                    "Artifact 删除文件失败，保留记录以便重试：artifact_id=%s error=%s",
                    record.artifact_id,
                    _safe_error_summary(exc),
                )
                raise ArtifactValidationError(
                    f"删除产物文件失败（{_safe_error_summary(exc)}）。"
                ) from exc
            try:
                self._record_path(artifact_id).unlink()
            except OSError as exc:
                raise ArtifactValidationError(
                    f"删除产物记录失败（{_safe_error_summary(exc)}）。"
                ) from exc
            return {"artifact_id": artifact_id, "deleted": True}

    # ------------------------------------------------------------ 内部实现

    def _read_verified_bytes(self, record: ArtifactRecord) -> bytes:
        """从记录确定路径读取并完整校验内容；失败统一按内容不可用抛安全错误。"""
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(record.storage_path, flags)
        except OSError as exc:
            raise ArtifactValidationError(
                f"产物内容不可用（{_safe_error_summary(exc)}）。"
            ) from exc
        try:
            try:
                fd_stat = os.fstat(fd)
                if not stat.S_ISREG(fd_stat.st_mode):
                    raise ArtifactValidationError("产物内容不可用，不是普通文件。")
                chunks: list[bytes] = []
                digest = hashlib.sha256()
                total = 0
                while True:
                    chunk = os.read(fd, COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    # 即使 fstat 时未超限，文件也可能在读取期间被替换/追加，必须持续限制。
                    if total > MAX_REGISTER_BYTES:
                        raise ArtifactValidationError("产物内容不可用，超过登记上限。")
                    digest.update(chunk)
                    chunks.append(chunk)
                data = b"".join(chunks)
            except OSError as exc:
                raise ArtifactValidationError(
                    f"产物内容不可用（{_safe_error_summary(exc)}）。"
                ) from exc
        finally:
            os.close(fd)
        if total != record.size or digest.hexdigest() != record.sha256:
            raise ArtifactValidationError("产物内容不可用，与记录校验不一致。")
        return data

    def _resolve_workspace_path(self, raw: str) -> tuple[Path, Path, Path]:
        """把用户路径解析成 Workspace Root 内的真实路径。

        与 workspace_tools 的安全解析保持等价：只允许根目录内、拒绝 ``..``、
        敏感路径与越界；返回 (工作区根, 解析后绝对路径, 相对根目录路径)。
        使用 self.workspace_root 而非全局 AppPaths，便于探针注入临时工作区。
        """
        root = self.workspace_root
        if not root.is_dir():
            raise ArtifactValidationError("工作区根目录不存在或不是目录。")
        text = str(raw or "").strip()
        if not text:
            raise ArtifactValidationError("路径不能为空。")
        candidate = Path(text)
        if ".." in candidate.parts:
            raise ArtifactValidationError("路径不能包含 ..，防止越出工作区。")
        absolute = candidate if candidate.is_absolute() else root / candidate
        try:
            resolved = absolute.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ArtifactValidationError(
                f"路径无法解析（{_safe_error_summary(exc)}）。"
            ) from exc
        if not _is_within(root, resolved):
            raise ArtifactValidationError("路径超出工作区范围。")
        try:
            relative = resolved.relative_to(root)
        except ValueError as exc:
            raise ArtifactValidationError("路径超出工作区范围。") from exc
        # 既检查用户原始路径、也检查解析后路径的敏感分量，防止经链接绕过敏感名单。
        raw_relative: Path | None = None
        if candidate.is_absolute():
            try:
                raw_relative = candidate.relative_to(root)
            except ValueError:
                raw_relative = None
        else:
            raw_relative = candidate
        if (raw_relative is not None and _is_sensitive_path(raw_relative)) or _is_sensitive_path(relative):
            raise ArtifactValidationError("路径涉及敏感文件或目录（.git/.env/密钥/凭据等），已拒绝访问。")
        return root, resolved, relative

    def _storage_path(
        self,
        user_id: str,
        session_id: str,
        run_id: str,
        artifact_id: str,
        suffix: str,
    ) -> Path:
        """内容文件路径：files/{namespace}/{artifact_id}{suffix}。

        namespace 由 user/session/run 派生，避免把原始标识直接拼进文件名；
        存储路径只写磁盘，绝不进入公开输出。
        """
        namespace = _namespace(user_id, session_id, run_id)
        return self.files_dir / namespace / f"{artifact_id}{suffix}"

    def _record_path(self, artifact_id: str) -> Path:
        # 所有公开 Service ID 入参先做格式校验，禁止 ../ 等路径穿越。
        return self.records_dir / f"{_validate_artifact_id(artifact_id)}.json"

    def _write_record(self, record: ArtifactRecord) -> None:
        """原子发布元数据：同目录临时文件 + fsync + os.replace。"""
        payload = record.to_record_dict()
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        _atomic_write(self._record_path(record.artifact_id), data)

    def _read_record_path(self, path: Path) -> ArtifactRecord | None:
        """读取单条元数据并把磁盘内容当作不可信输入严格校验。

        - artifact_id 必须为 32 位小写 hex 且与记录文件名 stem 一致；
        - storage_path 必须严格等于由归属三元组重算出的确定路径；
        - 任何损坏/不确定记录都跳过并记录只含 artifact_id 与异常类/errno 的
          安全日志，绝不删除文件或元数据，也不阻断其他正常记录的操作。
        """
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            # 记录不存在属于常规 404 流程，不是不确定记录，直接返回 None。
            return None
        except Exception as exc:
            logger.warning(
                "Artifact 元数据读取失败，已跳过：record=%s error=%s",
                path.name,
                _safe_error_summary(exc),
            )
            return None
        try:
            return self._validate_record_payload(payload, path.stem)
        except Exception as exc:
            logger.warning(
                "Artifact 元数据校验失败，已跳过：record=%s error=%s",
                path.name,
                _safe_error_summary(exc),
            )
            return None

    def _validate_record_payload(self, payload: Any, stem: str) -> ArtifactRecord:
        """把磁盘私有元数据按不可信输入逐字段校验，返回只含确定字段的记录。

        校验失败抛 ValueError（由 ``_read_record_path`` 捕获并跳过该记录）；
        绝不信任 ``storage_path`` 原文，最终存储路径一律用
        ``files_dir + namespace(user/session/run) + artifact_id + suffix`` 重算，
        防止磁盘 JSON 被篡改后把 cleanup/delete/download 引向 Artifact 根外。
        """
        if not isinstance(payload, dict):
            raise ValueError("record payload must be an object")
        artifact_id = payload.get("artifact_id")
        if not isinstance(artifact_id, str) or not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise ValueError("invalid artifact_id")
        if artifact_id != stem:
            raise ValueError("artifact_id does not match record filename")
        for key in ("user_id", "session_id", "run_id"):
            value = payload.get(key)
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
                or len(value) > MAX_OWNER_LENGTH
                or _CONTROL_CHAR_RE.search(value)
            ):
                raise ValueError(f"invalid {key}")
            if key == "user_id":
                user_id = value
            elif key == "session_id":
                session_id = value
            else:
                run_id = value
        filename = payload.get("filename")
        if not isinstance(filename, str):
            raise ValueError("invalid filename")
        safe_name = _sanitize_filename(filename)
        if safe_name != filename:
            raise ValueError("filename is not a normalized basename")
        suffix = payload.get("suffix")
        if not isinstance(suffix, str) or suffix not in SUPPORTED_SUFFIXES:
            raise ValueError("invalid suffix")
        if Path(filename).suffix.lower() != suffix:
            raise ValueError("filename suffix mismatch")
        content_type = payload.get("content_type")
        if not isinstance(content_type, str) or content_type != _content_type_for(suffix):
            raise ValueError("invalid content_type")
        size = payload.get("size")
        if type(size) is not int or size < 1 or size > MAX_REGISTER_BYTES:
            raise ValueError("invalid size")
        sha256 = payload.get("sha256")
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise ValueError("invalid sha256")
        summary = payload.get("summary")
        if summary is not None and (
            not isinstance(summary, str)
            or len(summary) > MAX_SUMMARY_CHARS
            or _clean_summary(summary) != summary
        ):
            raise ValueError("invalid summary")
        source_kind = payload.get("source_kind")
        if source_kind not in ("generated_text", "workspace_snapshot"):
            raise ValueError("invalid source_kind")
        source_path = payload.get("source_path")
        if source_kind == "generated_text":
            if source_path is not None:
                raise ValueError("generated_text must not have source_path")
        else:
            if (
                not isinstance(source_path, str)
                or not source_path.strip()
                or len(source_path) > MAX_SOURCE_PATH_CHARS
                or source_path != source_path.strip()
                or Path(source_path).is_absolute()
                or ".." in Path(source_path).parts
                or "\\" in source_path
                or _CONTROL_CHAR_RE.search(source_path)
            ):
                raise ValueError("invalid source_path")
            candidate = Path(source_path)
            if candidate.as_posix() != source_path or _is_sensitive_path(candidate):
                raise ValueError("invalid source_path")
        created_at = payload.get("created_at")
        if not isinstance(created_at, str):
            raise ValueError("invalid created_at")
        created_dt = _parse_dt(created_at)
        if created_dt is None or created_dt.tzinfo is None or created_dt.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if not (
            datetime(2000, 1, 1, tzinfo=timezone.utc)
            <= created_dt
            <= datetime(2100, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        ):
            raise ValueError("created_at out of range")
        retained = payload.get("retained", False)
        if type(retained) is not bool:
            raise ValueError("invalid retained")
        expires_at = payload.get("expires_at")
        if retained:
            if expires_at is not None:
                raise ValueError("retained record must not have expires_at")
        else:
            if not isinstance(expires_at, str):
                raise ValueError("non-retained record must have expires_at")
            expires_dt = _parse_dt(expires_at)
            if expires_dt is None or expires_dt.tzinfo is None or expires_dt.utcoffset() is None:
                raise ValueError("expires_at must be timezone-aware")
        storage_path = payload.get("storage_path")
        if not isinstance(storage_path, str) or not storage_path:
            raise ValueError("invalid storage_path")
        expected = self._storage_path(user_id, session_id, run_id, artifact_id, suffix)
        resolved_path = Path(storage_path).resolve(strict=False)
        if resolved_path != expected:
            raise ValueError("storage_path does not match deterministic path")
        if not _is_within(self.files_dir, resolved_path):
            raise ValueError("storage_path outside files dir")
        return ArtifactRecord(
            artifact_id=artifact_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            filename=filename,
            suffix=suffix,
            content_type=content_type,
            size=size,
            sha256=sha256,
            summary=summary,
            source_kind=source_kind,
            source_path=source_path,
            created_at=created_at,
            retained=retained,
            expires_at=expires_at,
            storage_path=str(expected),
        )

    def _lazy_cleanup(self) -> None:
        """轻量惰性清理：删除已过期且未保留的记录与对应文件。

        单条记录异常或不确定时跳过，绝不误删；日志只记录 artifact_id 与错误。
        """
        if not self.records_dir.is_dir():
            return
        # 先物化路径列表再遍历，避免边遍历边删除时同一目录条目被重新产出，
        # 导致后续 read_text 抛 FileNotFoundError 造成误报。
        for record_path in list(self.records_dir.glob("*.json")):
            record = self._read_record_path(record_path)
            if record is None or record.retained:
                continue
            expires_at = _parse_dt(record.expires_at)
            if expires_at is None or expires_at >= datetime.now(timezone.utc):
                continue
            file_path = Path(record.storage_path)
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError as exc:
                logger.warning(
                    "Artifact 惰性清理删除文件失败，跳过该记录：artifact_id=%s error=%s",
                    record.artifact_id,
                    _safe_error_summary(exc),
                )
                continue
            try:
                record_path.unlink()
            except OSError as exc:
                logger.warning(
                    "Artifact 惰性清理删除记录失败：artifact_id=%s error=%s",
                    record.artifact_id,
                    _safe_error_summary(exc),
                )


# ---------------------------------------------------------------- 工具函数


def _owner_text(value: str | None) -> str:
    """归属字段（user/session/run）规范化：空值回落为 default，避免 None 写入契约字段。"""
    text = str(value or "").strip()
    return text or "default"


def _normalize_owner(value: str | None, *, label: str) -> str:
    """归属字段统一规范化（创建/登记入口使用）。

    原始值在 trim 前检查控制字符：纯空白（strip 后为空）回落为 default；strip 后
    非空但原始值含任意控制字符（bad\\n、bad\\t、x\\ny）一律拒绝。非空最终规范化
    值长度不超过 MAX_OWNER_LENGTH，与磁盘记录校验规则完全一致，杜绝“创建成功但
    下一次读取因 owner 元数据不合法而跳过”。
    """
    raw = str(value or "")
    text = raw.strip()
    if not text:
        return "default"
    if _CONTROL_CHAR_RE.search(raw):
        raise ArtifactValidationError(f"{label} 不能包含控制字符。")
    if len(text) > MAX_OWNER_LENGTH:
        raise ArtifactValidationError(f"{label} 长度不能超过 {MAX_OWNER_LENGTH} 个字符。")
    return text


def _validate_artifact_id(artifact_id: str) -> str:
    """校验公开入参 artifact_id：必须为 32 位小写十六进制，禁止路径穿越。"""
    text = str(artifact_id or "").strip()
    if not _ARTIFACT_ID_RE.fullmatch(text):
        raise ArtifactValidationError("artifact_id 必须是 32 位小写十六进制字符串。")
    return text


def _safe_error_summary(exc: BaseException) -> str:
    """把异常压缩成可对外/进日志的安全摘要。

    只保留异常类名与 errno，绝不拼接 OSError 的 str()：原文可能包含主机绝对路径
    （Artifact 根、Workspace Root 等），会泄露目录结构并破坏公开错误边界。
    """
    errno = getattr(exc, "errno", None)
    if isinstance(errno, int):
        return f"{type(exc).__name__}(errno={errno})"
    return type(exc).__name__


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_at_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=RETENTION_DAYS)).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _namespace(user_id: str, session_id: str, run_id: str) -> str:
    """从归属三元组派生存储命名空间（16 位十六进制），避免敏感标识出现在文件名中。"""
    raw = f"{_owner_text(user_id)}\u0000{_owner_text(session_id)}\u0000{_owner_text(run_id)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _sanitize_filename(filename: str) -> str:
    """只保留 basename，去除 NUL/控制字符，长度不超过 180，扩展名必须受支持。"""
    text = str(filename or "")
    # 同时兼容 / 与 \ 分隔，避免 Windows 风格路径把目录带进文件名。
    text = text.replace("\\", "/")
    base = text.rsplit("/", 1)[-1].strip()
    base = _CONTROL_CHAR_RE.sub("", base)
    if not base:
        raise ArtifactValidationError("文件名不能为空。")
    if len(base) > MAX_FILENAME_LENGTH:
        raise ArtifactValidationError(f"文件名过长，最多 {MAX_FILENAME_LENGTH} 个字符。")
    suffix = Path(base).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ArtifactValidationError(
            f"暂不支持 {suffix or '无扩展名'} 格式，当前支持："
            + "、".join(sorted(SUPPORTED_SUFFIXES))
            + "。"
        )
    return base


def _content_type_for(suffix: str) -> str:
    return _CONTENT_TYPES.get(suffix, "application/octet-stream")


def _clean_summary(summary: str | None) -> str | None:
    """摘要可选、最多 500 字符、单行；空白折叠后为空则视为未提供。"""
    if summary is None:
        return None
    text = " ".join(str(summary).split())
    if not text:
        return None
    if len(text) > MAX_SUMMARY_CHARS:
        raise ArtifactValidationError(f"摘要最多 {MAX_SUMMARY_CHARS} 个字符。")
    return text


def _atomic_write(path: Path, data: bytes) -> None:
    """同目录临时文件 + fsync + os.replace 原子发布；权限 0600、目录 0700。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    fd, tmp_name = tempfile.mkstemp(prefix=".artifact-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, FILE_MODE)
        except OSError:
            pass
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
