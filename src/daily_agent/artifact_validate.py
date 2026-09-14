"""Artifact 内容校验：文本解码与二进制魔数。

从 artifact_service 拆出，避免存储服务超过 1000 行。校验失败一律抛出
ArtifactValidationError，由 Service / Tool / API 映射为 400。
"""

from __future__ import annotations

import json

from daily_agent.artifact_models import BINARY_SUFFIXES, TEXT_SUFFIXES


class ArtifactValidationError(ValueError):
    """输入不合法（格式、大小、路径、内容等）时抛出，映射为 400。"""


def validate_artifact_bytes(data: bytes, *, suffix: str, filename: str, max_bytes: int) -> None:
    """按扩展名分流：文本做解码校验，二进制交付物只核大小与文件头魔数。"""
    if suffix in TEXT_SUFFIXES:
        validate_text_bytes(data, suffix=suffix, filename=filename, max_bytes=max_bytes)
        return
    if suffix not in BINARY_SUFFIXES:
        raise ArtifactValidationError(f"暂不支持 {suffix} 格式。")
    if not data:
        raise ArtifactValidationError("内容不能为空。")
    if len(data) > max_bytes:
        raise ArtifactValidationError(
            f"文件超过 {max_bytes // 1024 // 1024}MB 上限。"
        )
    validate_binary_magic(data, suffix=suffix, filename=filename)


def validate_binary_magic(data: bytes, *, suffix: str, filename: str) -> None:
    """用文件头魔数确认扩展名与内容一致，防止把任意字节登记成办公文档。"""
    if suffix in {".docx", ".xlsx"}:
        if not data.startswith(b"PK"):
            raise ArtifactValidationError(f"{filename} 不是有效的 Office Open XML 文件。")
        return
    if suffix == ".pdf":
        if not data.lstrip().startswith(b"%PDF"):
            raise ArtifactValidationError(f"{filename} 不是有效的 PDF 文件。")
        return
    if suffix == ".png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ArtifactValidationError(f"{filename} 不是有效的 PNG 图片。")
        return
    if suffix in {".jpg", ".jpeg"}:
        if not data.startswith(b"\xff\xd8\xff"):
            raise ArtifactValidationError(f"{filename} 不是有效的 JPEG 图片。")
        return
    if suffix == ".webp":
        if not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
            raise ArtifactValidationError(f"{filename} 不是有效的 WebP 图片。")
        return
    raise ArtifactValidationError(f"暂不支持 {suffix} 格式。")


def validate_text_bytes(data: bytes, *, suffix: str, filename: str, max_bytes: int) -> None:
    """文本内容校验：非空、未超限、无 NUL、可安全解码；JSON 还必须可解析。"""
    if not data:
        raise ArtifactValidationError("内容不能为空。")
    if len(data) > max_bytes:
        raise ArtifactValidationError(
            f"文件超过 {max_bytes // 1024 // 1024}MB 上限。"
        )
    if b"\x00" in data:
        raise ArtifactValidationError(f"文件 {filename} 是二进制或包含 NUL 内容，已拒绝。")
    text = decode_text(data)
    if suffix == ".json":
        try:
            json.loads(text)
        except ValueError as exc:
            raise ArtifactValidationError(f"JSON 内容无法解析：{exc}") from exc


def decode_text(data: bytes) -> str:
    """安全解码文本：优先 UTF-8 BOM，其次 UTF-8，最后 GB18030。"""
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ArtifactValidationError("内容无法安全解码为文本（不是 UTF-8/GB18030）。")
