from __future__ import annotations

from pathlib import Path

from daily_agent.app_context import ApplicationContext
from daily_agent.config import get_config_path, load_settings
from daily_agent.paths import AppPaths
from daily_agent.services.report_service import _resolve_daily_report_scan_script
from daily_agent.skill_catalog import SkillCatalog


def test_app_paths_use_data_dir_instead_of_cwd(tmp_path):
    workspace_root = tmp_path / "workspace" / "nested"
    app_home = tmp_path / "agent-home"
    data_dir = tmp_path / "agent-data"

    paths = AppPaths.discover(
        workspace_root=workspace_root,
        app_home=app_home,
        data_dir=data_dir,
    )

    assert paths.workspace_root == workspace_root.resolve()
    assert paths.app_home == app_home.resolve()
    assert paths.data_dir == data_dir.resolve()
    assert paths.config_path == (data_dir / "config.toml").resolve()
    assert paths.skill_registry_path == (data_dir / "skills" / "registry.json").resolve()


def test_skill_catalog_defaults_to_context_paths(tmp_path):
    paths = AppPaths.discover(
        workspace_root=tmp_path / "workspace",
        data_dir=tmp_path / "state",
    )

    catalog = SkillCatalog(paths=paths)

    assert catalog.imported_home == (paths.data_dir / "skills" / "imported").resolve()
    assert catalog.registry_path == (paths.data_dir / "skills" / "registry.json").resolve()


def test_application_context_exposes_resolved_metadata(tmp_path):
    paths = AppPaths.discover(
        workspace_root=tmp_path / "workspace",
        app_home=tmp_path / "app-home",
        data_dir=tmp_path / "app-data",
    )

    context = ApplicationContext.build(paths=paths)
    metadata = context.to_metadata()

    assert metadata["config_path"] == str(get_config_path(paths=paths))
    assert metadata["data_dir"] == str(paths.data_dir)
    assert metadata["workspace_root"] == str(paths.workspace_root)


def test_sb_agent_env_vars_take_precedence(monkeypatch):
    monkeypatch.delenv("SB_AGENT_CONFIG", raising=False)
    monkeypatch.delenv("DAILY_AGENT_CONFIG", raising=False)
    monkeypatch.setenv("SB_AGENT_MYSQL_DATABASE", "sb_primary")
    monkeypatch.setenv("DAILY_AGENT_MYSQL_DATABASE", "daily_legacy")

    settings = load_settings()

    assert settings.mysql_database == "sb_primary"


def test_legacy_daily_agent_env_vars_still_work(monkeypatch):
    monkeypatch.delenv("SB_AGENT_CONFIG", raising=False)
    monkeypatch.delenv("DAILY_AGENT_CONFIG", raising=False)
    monkeypatch.delenv("SB_AGENT_MYSQL_DATABASE", raising=False)
    monkeypatch.setenv("DAILY_AGENT_MYSQL_DATABASE", "daily_legacy")

    settings = load_settings()

    assert settings.mysql_database == "daily_legacy"


def test_agno_native_telemetry_env_is_visible_to_settings(monkeypatch):
    monkeypatch.delenv("SB_AGENT_AGNO_TELEMETRY", raising=False)
    monkeypatch.delenv("DAILY_AGENT_AGNO_TELEMETRY", raising=False)
    monkeypatch.setenv("AGNO_TELEMETRY", "true")

    settings = load_settings()

    assert settings.agno_telemetry is True


def test_daily_report_script_path_does_not_fallback_to_workspace_hidden_dir(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    paths = AppPaths.discover(
        workspace_root=workspace_root,
        data_dir=tmp_path / "state",
    )
    monkeypatch.chdir(workspace_root)

    script_path = _resolve_daily_report_scan_script(paths=paths)

    assert workspace_root / ".sb-agent" not in script_path.parents
    assert script_path.name == "scan_commits.py"
