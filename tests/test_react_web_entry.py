import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from daily_agent.api import NEXT_WEB_DIR, create_app
from daily_agent.web_assets import (
    HASHED_ASSET_CACHE_CONTROL,
    STATIC_CACHE_CONTROL,
    mount_workbench_frontends,
)


def test_react_build_is_default_and_next_remains_compatible() -> None:
    """React 构建成为默认入口，同时保留 `/next/` 兼容地址。"""
    index_path = NEXT_WEB_DIR / "index.html"

    assert index_path.exists()
    assert index_path.is_file()

    client = TestClient(create_app())
    response = client.get("/")
    alias_response = client.get("/next/")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert "/next/assets/" in response.text
    assert response.headers["cache-control"] == STATIC_CACHE_CONTROL
    assert alias_response.status_code == 200
    assert alias_response.text == response.text
    assert alias_response.headers["cache-control"] == STATIC_CACHE_CONTROL


def test_legacy_workspace_has_query_and_path_fallbacks() -> None:
    """切换后必须能在不重启服务的情况下回到旧版。"""
    client = TestClient(create_app())

    query_response = client.get("/?legacy=1")
    path_response = client.get("/legacy/")

    for response in (query_response, path_response):
        assert response.status_code == 200
        assert '<div class="app-shell">' in response.text
        assert "/assets/styles.css" in response.text
        assert '<div id="root"></div>' not in response.text
        assert response.headers["cache-control"] == STATIC_CACHE_CONTROL


def test_react_build_contains_only_relative_workspace_assets() -> None:
    """入口引用必须固定在 `/next/` 下，避免与旧版 `/assets` 资源互相覆盖。"""
    index_source = (NEXT_WEB_DIR / "index.html").read_text(encoding="utf-8")

    assert 'src="/next/assets/' in index_source
    assert 'href="/next/assets/' in index_source
    assert 'src="/assets/' not in index_source
    assert Path(NEXT_WEB_DIR).name == "web_dist"


def test_react_hashed_assets_are_immutable_but_html_is_not_cached() -> None:
    """内容哈希资源可长期缓存，入口 HTML 必须保持可切换。"""
    client = TestClient(create_app())
    index_response = client.get("/")
    asset_path = re.search(r'src="(/next/assets/[^"]+\.js)"', index_response.text)

    assert asset_path is not None
    asset_response = client.get(asset_path.group(1))
    assert asset_response.status_code == 200
    assert asset_response.headers["cache-control"] == HASHED_ASSET_CACHE_CONTROL
    assert index_response.headers["cache-control"] == STATIC_CACHE_CONTROL


def test_health_exposes_frontend_build_identity() -> None:
    """运行状态必须能核对当前默认前端构建。"""
    payload = TestClient(create_app()).get("/health").json()

    assert payload["frontend"]["available"] is True
    assert re.fullmatch(r"[0-9a-f]{12}", payload["frontend"]["build_id"])
    assert payload["frontend"]["default_entry"] == "/"
    assert payload["frontend"]["legacy_entry"] == "/?legacy=1"


def test_react_bundle_tool_contract_includes_workspace_and_shell_sources() -> None:
    """嵌入后端的 React 产物必须包含完整 Tool source_type 契约。"""
    assets_dir = NEXT_WEB_DIR / "assets"
    index_assets = sorted(assets_dir.glob("index-*.js"))
    assert index_assets, "React index bundle is missing"
    source = index_assets[-1].read_text(encoding="utf-8")
    assert "`workspace`,`shell`]" in source


def test_missing_react_build_falls_back_to_legacy(tmp_path: Path) -> None:
    """后端已升级但 React 产物缺失时，根入口仍应保持可操作。"""
    legacy_directory = tmp_path / "legacy"
    react_directory = tmp_path / "react"
    legacy_directory.mkdir()
    legacy_directory.joinpath("index.html").write_text("<main>legacy fallback</main>", encoding="utf-8")
    app = FastAPI()
    mount_workbench_frontends(app, legacy_directory, react_directory)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.text == "<main>legacy fallback</main>"
    assert response.headers["cache-control"] == STATIC_CACHE_CONTROL
