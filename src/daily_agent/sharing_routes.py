"""指定成员只读会话分享 HTTP 接口。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from daily_agent.artifact_service import ArtifactNotFoundError, ArtifactService, ArtifactValidationError
from daily_agent.auth_dependencies import require_user
from daily_agent.auth_models import AuthenticatedUser
from daily_agent.sharing_service import SharingError, SharingService


class ShareCreateRequest(BaseModel):
    """把自己的会话只读分享给一个已注册成员。"""

    session_id: str = Field(min_length=1, max_length=128, description="当前用户拥有的会话 ID")
    recipient_email: str = Field(min_length=3, max_length=254, description="接收成员的登录邮箱")


def register_sharing_routes(
    app: FastAPI,
    *,
    sharing_service: SharingService,
    artifact_service: ArtifactService,
) -> None:
    """注册登录用户间的定向分享、撤销、查看、产物下载和复制接口。"""

    @app.post(
        "/shares",
        summary="把自己的会话只读分享给指定成员",
        description="接收人必须是启用的已注册成员；接口不会创建匿名或公开链接。",
    )
    def create_share(payload: ShareCreateRequest, user: AuthenticatedUser = Depends(require_user)):
        try:
            return {"share": sharing_service.create_share(
                owner_user_id=user.id,
                session_id=payload.session_id,
                recipient_email=payload.recipient_email,
            )}
        except SharingError as exc:
            raise _http_error(exc) from exc

    @app.get("/shares/sent", summary="查看自己发出的有效分享")
    def list_sent_shares(
        session_id: str | None = Query(default=None, max_length=128, description="可选的会话 ID 筛选"),
        user: AuthenticatedUser = Depends(require_user),
    ):
        return {"items": sharing_service.list_sent(user.id, session_id)}

    @app.get("/shares/received", summary="查看别人分享给自己的会话")
    def list_received_shares(user: AuthenticatedUser = Depends(require_user)):
        return {"items": sharing_service.list_received(user.id)}

    @app.get(
        "/shares/received/{share_id}",
        summary="只读查看别人分享给自己的完整会话",
        description="最多返回前 200 条消息及可下载产物元数据，不返回原用户运行凭据、附件或私有连接。",
    )
    def get_received_share(
        share_id: str = Path(min_length=36, max_length=36),
        user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            share = sharing_service.get_received_share(share_id=share_id, recipient_user_id=user.id)
        except SharingError as exc:
            raise _http_error(exc) from exc
        try:
            records, _ = artifact_service.list_records(
                user_id=share["owner_user_id"],
                session_id=share["session_id"],
                limit=100,
                offset=0,
            )
        except ArtifactValidationError:
            records = []
        artifacts = []
        for record in records:
            item = record.to_public_dict()
            item["download_url"] = f"/shares/received/{share_id}/artifacts/{record.artifact_id}/download"
            artifacts.append(item)
        share.pop("owner_user_id", None)
        share.pop("recipient_user_id", None)
        return {"share": share, "artifacts": artifacts}

    @app.get(
        "/shares/received/{share_id}/artifacts/{artifact_id}/download",
        summary="下载共享会话中的产物",
        description="同时校验当前成员仍拥有该分享，以及产物确实属于分享的原会话。",
    )
    def download_shared_artifact(
        share_id: str = Path(min_length=36, max_length=36),
        artifact_id: str = Path(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$"),
        user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            share = sharing_service.get_received_share(share_id=share_id, recipient_user_id=user.id)
            data, record = artifact_service.download_content(artifact_id, user_id=share["owner_user_id"])
            if record.session_id != share["session_id"]:
                raise ArtifactNotFoundError("产物不属于这个共享会话。")
        except SharingError as exc:
            raise _http_error(exc) from exc
        except (ArtifactNotFoundError, ArtifactValidationError) as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "shared_artifact_not_found", "message": str(exc)},
            ) from exc
        encoded_name = quote(record.filename)
        disposition = (
            f"attachment; filename*=utf-8''{encoded_name}"
            if encoded_name != record.filename
            else f'attachment; filename="{record.filename}"'
        )
        return Response(content=data, media_type=record.content_type, headers={"content-disposition": disposition})

    @app.post("/shares/received/{share_id}/copy", summary="把共享会话复制到自己的会话列表")
    def copy_received_share(
        share_id: str = Path(min_length=36, max_length=36),
        user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            session_id = sharing_service.copy_received_share(share_id=share_id, recipient_user_id=user.id)
        except SharingError as exc:
            raise _http_error(exc) from exc
        return {"status": "success", "session_id": session_id}

    @app.delete("/shares/{share_id}", summary="撤销自己发出的会话分享")
    def revoke_share(
        share_id: str = Path(min_length=36, max_length=36),
        user: AuthenticatedUser = Depends(require_user),
    ):
        if not sharing_service.revoke_share(share_id=share_id, owner_user_id=user.id):
            raise HTTPException(
                status_code=404,
                detail={"code": "share_not_found", "message": "分享不存在或已经被撤销。"},
            )
        return {"status": "success", "share_id": share_id}


def _http_error(exc: SharingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})
