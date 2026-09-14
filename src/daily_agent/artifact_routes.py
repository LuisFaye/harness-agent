"""Artifact REST API 路由。

独立成模块是为了保持 api.py 单文件行数不超过 1000，同时让 Swagger 描述集中维护。
所有接口都从服务端登录会话取得 user_id，并按该身份校验所有权。
公开响应只含 ArtifactPublic 模型，不包含 storage_path 或任何主机绝对路径。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query
from fastapi.responses import Response

from daily_agent.auth_dependencies import require_user
from daily_agent.auth_models import AuthenticatedUser
from daily_agent.artifact_models import (
    ArtifactDeleteResponse,
    ArtifactListResponse,
    ArtifactPublic,
    ArtifactRetentionRequest,
)
from daily_agent.artifact_service import (
    ArtifactNotFoundError,
    ArtifactService,
    ArtifactValidationError,
)


def register_artifact_routes(app: FastAPI, artifact_service: ArtifactService) -> None:
    """在 app 上注册 Artifact 的五个 REST 接口。"""

    def _not_found(exc: ArtifactNotFoundError) -> HTTPException:
        """所有权校验失败或记录不存在统一返回 404，避免泄露记录是否存在。"""
        return HTTPException(
            status_code=404,
            detail={"code": "artifact_not_found", "message": str(exc)},
        )

    def _public(record) -> ArtifactPublic:
        return ArtifactPublic(**record.to_public_dict())

    @app.get(
        "/artifacts",
        response_model=ArtifactListResponse,
        summary="分页查询当前用户的 Artifact",
        description=(
            "返回 user_id 名下 Artifact 的公开元数据，按创建时间倒序；"
            "session_id 与 run_id 至少提供一个。不返回正文与任何绝对路径。"
        ),
        responses={
            400: {"description": "查询参数不合法（limit/offset 越界等）"},
            422: {"description": "session_id 与 run_id 都没有提供"},
        },
    )
    def list_artifacts(
        session_id: str | None = Query(default=None, max_length=128, description="按会话 ID 精确筛选"),
        run_id: str | None = Query(default=None, max_length=128, description="按 Agent 运行 ID 精确筛选"),
        limit: int = Query(default=20, ge=1, le=100, description="本页最多返回 1-100 条"),
        offset: int = Query(default=0, ge=0, description="从第几条匹配记录开始返回"),
        user: AuthenticatedUser = Depends(require_user),
    ):
        if not session_id and not run_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "artifact_filter_required",
                    "message": "session_id 与 run_id 至少提供一个。",
                },
            )
        try:
            records, total = artifact_service.list_records(
                user_id=user.id,
                session_id=session_id,
                run_id=run_id,
                limit=limit,
                offset=offset,
            )
        except ArtifactValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "artifact_invalid", "message": str(exc)},
            ) from exc
        return ArtifactListResponse(
            items=[_public(record) for record in records],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/artifacts/{artifact_id}",
        response_model=ArtifactPublic,
        summary="按 ID 查看一条 Artifact 的公开元数据",
        description="按 user_id 所有权读取公开元数据；不返回正文、storage_path 与绝对路径。",
        responses={
            404: {"description": "产物不存在或不属于当前 user_id"},
            422: {"description": "artifact_id 不是 32 位小写十六进制字符串"},
        },
    )
    def get_artifact(
        artifact_id: str = PathParam(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-f]{32}$",
            description="32 位小写十六进制产物 ID",
        ),
        user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            record = artifact_service.get(artifact_id, user_id=user.id)
        except ArtifactNotFoundError as exc:
            raise _not_found(exc) from exc
        return _public(record)

    @app.get(
        "/artifacts/{artifact_id}/download",
        summary="下载一条 Artifact 文件内容",
        description=(
            "按 user_id 所有权校验（语义与查看元数据一致）后，在服务锁内对同一受控 "
            "文件描述符完成读取与完整性校验：拒绝符号链接/非普通文件，实际字节数必须"
            "等于记录的 size，SHA-256 必须等于记录的 sha256；校验通过后返回已验证的"
            "内存字节快照，Content-Type 与附件文件名正确。内容文件缺失、被替换为符号"
            "链接或非普通文件、大小或哈希不一致时统一返回 404 "
            "artifact_content_unavailable，响应不包含任何主机绝对路径。"
        ),
        responses={
            200: {"description": "产物文件内容（已验证字节快照，Content-Type 与附件文件名正确）"},
            404: {
                "description": (
                    "产物不存在或不属于当前 user_id；或内容不可用"
                    "（缺失/符号链接/非普通文件/大小或哈希不一致）"
                )
            },
            422: {"description": "artifact_id 不是 32 位小写十六进制字符串"},
        },
    )
    def download_artifact(
        artifact_id: str = PathParam(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-f]{32}$",
            description="32 位小写十六进制产物 ID",
        ),
        user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            data, record = artifact_service.download_content(artifact_id, user_id=user.id)
        except ArtifactNotFoundError as exc:
            raise _not_found(exc) from exc
        except ArtifactValidationError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_content_unavailable", "message": str(exc)},
            ) from exc
        # 与 FileResponse 相同的附件文件名编码：非 ASCII 用 RFC 5987 filename*。
        quoted_filename = quote(record.filename)
        if quoted_filename != record.filename:
            disposition = f"attachment; filename*=utf-8''{quoted_filename}"
        else:
            disposition = f'attachment; filename="{record.filename}"'
        return Response(
            content=data,
            media_type=record.content_type,
            headers={"content-disposition": disposition},
        )

    @app.patch(
        "/artifacts/{artifact_id}/retention",
        response_model=ArtifactPublic,
        summary="切换一条 Artifact 的保留状态",
        description=(
            "retained=true 时永久保留（expires_at 为 null）；retained=false 时"
            "从当前时间重新计算 30 天过期时间。返回更新后的公开记录。"
        ),
        responses={
            404: {"description": "产物不存在或不属于当前 user_id"},
            422: {"description": "artifact_id 不合法或请求体缺少 retained"},
        },
    )
    def set_artifact_retention(
        request: ArtifactRetentionRequest,
        artifact_id: str = PathParam(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-f]{32}$",
            description="32 位小写十六进制产物 ID",
        ),
        user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            record = artifact_service.set_retained(
                artifact_id,
                retained=request.retained,
                user_id=user.id,
            )
        except ArtifactNotFoundError as exc:
            raise _not_found(exc) from exc
        except ArtifactValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "artifact_invalid", "message": str(exc)},
            ) from exc
        return _public(record)

    @app.delete(
        "/artifacts/{artifact_id}",
        response_model=ArtifactDeleteResponse,
        summary="硬删除一条 Artifact",
        description="按 user_id 所有权校验后同时删除内容文件与元数据；重复删除返回 404。",
        responses={
            404: {"description": "产物不存在或不属于当前 user_id"},
            422: {"description": "artifact_id 不是 32 位小写十六进制字符串"},
        },
    )
    def delete_artifact(
        artifact_id: str = PathParam(
            min_length=32,
            max_length=32,
            pattern=r"^[0-9a-f]{32}$",
            description="32 位小写十六进制产物 ID",
        ),
        user: AuthenticatedUser = Depends(require_user),
    ):
        try:
            result = artifact_service.delete(artifact_id, user_id=user.id)
        except ArtifactNotFoundError as exc:
            raise _not_found(exc) from exc
        except ArtifactValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "artifact_invalid", "message": str(exc)},
            ) from exc
        return ArtifactDeleteResponse(**result)
