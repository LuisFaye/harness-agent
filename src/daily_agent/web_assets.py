"""浏览器工作台静态资源的缓存策略。"""

import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

STATIC_CACHE_CONTROL = "no-store, max-age=0"
HASHED_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class NoCacheStaticFiles(StaticFiles):
    """确保浏览器始终加载同一版本的前端模块和样式。"""

    async def get_response(self, path, scope):
        # 工作台由多个互相依赖的 JavaScript 和 CSS 文件组成。
        # 如果浏览器只缓存了其中一部分，页面结构与样式就会版本错位，
        # 因此本地工作台统一禁用静态资源缓存。
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = STATIC_CACHE_CONTROL
        return response


class ReactStaticFiles(StaticFiles):
    """为 React 入口和内容哈希资源设置不同缓存策略。

    Vite 生成的 assets 文件名带内容哈希，文件内容变化时 URL 也会变化，
    因此可以安全长期缓存；HTML 必须每次重新校验，才能引用到最新一组哈希资源。
    """

    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or not _accepts_html(scope) or Path(str(path)).suffix:
                raise
            index_path = Path(self.directory) / "index.html"
            if not index_path.is_file():
                raise
            return FileResponse(index_path, headers={"Cache-Control": STATIC_CACHE_CONTROL})
        if response.status_code == 404 and _accepts_html(scope) and not Path(str(path)).suffix:
            index_path = Path(self.directory) / "index.html"
            if index_path.is_file():
                return FileResponse(index_path, headers={"Cache-Control": STATIC_CACHE_CONTROL})
        response.headers["Cache-Control"] = (
            HASHED_ASSET_CACHE_CONTROL
            if str(path).lstrip("/").startswith("assets/")
            else STATIC_CACHE_CONTROL
        )
        return response


def _accepts_html(scope) -> bool:
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }
    return "text/html" in headers.get("accept", "")


def frontend_build_metadata(directory: Path) -> dict[str, object]:
    """返回当前 React 构建的稳定标识，不暴露本机路径。

    build_id 只基于入口 HTML 内容。它会随资源哈希变化，足以用来核对浏览器、
    API 进程和当前提交是否加载了同一份前端构建。
    """

    index_path = directory / "index.html"
    if not index_path.is_file():
        return {"available": False, "build_id": None}
    try:
        digest = hashlib.sha256(index_path.read_bytes()).hexdigest()[:12]
    except OSError:
        return {"available": False, "build_id": None}
    return {"available": True, "build_id": digest}


def mount_workbench_frontends(app: FastAPI, legacy_directory: Path, react_directory: Path) -> None:
    """挂载 React 默认工作台和一个发布周期内可用的旧版回退入口。"""

    if legacy_directory.exists():
        # 旧版模块仍引用绝对 `/assets`，因此回退周期内保留原资源路径。
        app.mount("/assets", NoCacheStaticFiles(directory=legacy_directory), name="assets")
        app.mount("/legacy", NoCacheStaticFiles(directory=legacy_directory, html=True), name="legacy-web")
    if react_directory.exists():
        # React 构建固定使用 `/next/assets`，默认入口与兼容别名可共享同一份哈希资源。
        app.mount("/next", ReactStaticFiles(directory=react_directory, html=True), name="react-web")
    if not legacy_directory.exists() and not react_directory.exists():
        return

    def web_index(legacy: bool = Query(default=False)):
        # 查询参数让排障人员无需改配置或重启进程即可临时切回旧版。
        legacy_index = legacy_directory / "index.html"
        react_index = react_directory / "index.html"
        if legacy:
            if not legacy_index.is_file():
                raise HTTPException(status_code=404, detail="旧版工作台不可用。")
            index_path = legacy_index
        elif react_index.is_file():
            index_path = react_index
        elif legacy_index.is_file():
            index_path = legacy_index
        else:
            raise HTTPException(status_code=404, detail="工作台构建不可用。")
        return FileResponse(index_path, headers={"Cache-Control": STATIC_CACHE_CONTROL})

    app.add_api_route("/", web_index, methods=["GET"], include_in_schema=False)
    if react_directory.exists():
        # 放在全部 API 路由之后挂载时，根挂载只接管前端页面与未知路径。
        # html=True 让 /login、/register、/agent/<session> 等刷新后仍回到 React 入口。
        app.mount("/", ReactStaticFiles(directory=react_directory, html=True), name="react-spa")
