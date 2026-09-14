"""Artifact 磁盘记录模型（只依赖标准库，不依赖 artifact_service）。

职责：
- 定义磁盘上的一条完整 Artifact 记录 ``ArtifactRecord``（含私有存储路径）；
- 公开输出只能通过 ``to_public_dict()`` 获得，返回值绝不包含 ``storage_path``、
  Artifact 根路径或主机绝对路径；
- ``to_record_dict()`` 序列化为磁盘私有元数据（包含 ``storage_path``，仅存本地，
  不对外；不冗余落盘派生的 download_url）。

本模块刻意保持无依赖：artifact_service 引用它时不会产生循环导入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote


@dataclass(frozen=True)
class ArtifactRecord:
    """磁盘上的一条 Artifact 完整记录（含私有存储路径）。

    公开输出只能通过 ``to_public_dict()`` 获得，该方法的返回值绝不包含
    ``storage_path``、Artifact 根路径或主机绝对路径。
    """

    artifact_id: str
    user_id: str
    session_id: str
    run_id: str
    filename: str
    suffix: str
    content_type: str
    size: int
    sha256: str
    summary: str | None
    source_kind: str
    source_path: str | None
    created_at: str
    retained: bool
    expires_at: str | None
    storage_path: str

    def to_public_dict(self) -> dict[str, Any]:
        """转换为公开记录：剥离 storage_path，只保留契约字段。"""
        return {
            "artifact_id": self.artifact_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "filename": self.filename,
            "suffix": self.suffix,
            "content_type": self.content_type,
            "size": self.size,
            "sha256": self.sha256,
            "summary": self.summary,
            "source_kind": self.source_kind,
            "source_path": self.source_path,
            "created_at": self.created_at,
            "retained": self.retained,
            "expires_at": self.expires_at,
            # user_id 必须安全编码后拼进下载地址，保证非 default 用户拿到
            # 可直接使用的公开 URL；地址不含 session/run 与任何主机绝对路径。
            "download_url": (
                f"/artifacts/{self.artifact_id}/download"
                f"?user_id={quote(self.user_id, safe='')}"
            ),
        }

    def to_record_dict(self) -> dict[str, Any]:
        """序列化为磁盘私有元数据（包含 storage_path，仅存本地，不对外）。

        不冗余落盘派生的 download_url；读取旧记录时该字段被忽略，保持兼容。
        """
        payload = self.to_public_dict()
        payload.pop("download_url", None)
        payload["storage_path"] = self.storage_path
        return payload
