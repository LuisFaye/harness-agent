from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_APP_DIRNAME = ".sb-agent"


@dataclass(frozen=True)
class AppPaths:
    """统一管理应用运行时路径。"""

    app_home: Path
    workspace_root: Path
    data_dir: Path
    config_path: Path
    log_dir: Path
    plans_dir: Path
    skills_home: Path
    imported_skills_home: Path
    skill_sources_home: Path
    skill_registry_path: Path
    capability_registry_path: Path
    mcp_registry_path: Path
    database_connection_registry_path: Path
    database_connection_home: Path
    database_interaction_registry_path: Path
    react_pause_legacy_path: Path
    skill_session_legacy_path: Path

    @classmethod
    def discover(
        cls,
        *,
        app_home: str | Path | None = None,
        workspace_root: str | Path | None = None,
        data_dir: str | Path | None = None,
        config_path: str | Path | None = None,
        log_dir: str | Path | None = None,
    ) -> "AppPaths":
        resolved_workspace = _resolve_workspace_root(workspace_root)
        resolved_app_home = _resolve_app_home(app_home)
        resolved_data_dir = _resolve_data_dir(data_dir, resolved_app_home)
        resolved_config_path = _resolve_config_path(config_path, resolved_data_dir)
        resolved_log_dir = _resolve_log_dir(log_dir, resolved_data_dir)
        skills_home = resolved_data_dir / "skills"
        imported_home = skills_home / "imported"
        return cls(
            app_home=resolved_app_home,
            workspace_root=resolved_workspace,
            data_dir=resolved_data_dir,
            config_path=resolved_config_path,
            log_dir=resolved_log_dir,
            plans_dir=resolved_data_dir / "plans",
            skills_home=skills_home,
            imported_skills_home=imported_home,
            skill_sources_home=skills_home / "sources",
            skill_registry_path=skills_home / "registry.json",
            capability_registry_path=resolved_data_dir / "capabilities" / "tools.json",
            mcp_registry_path=resolved_data_dir / "capabilities" / "mcp_servers.json",
            database_connection_registry_path=resolved_data_dir / "capabilities" / "database_connections.json",
            database_connection_home=resolved_data_dir / "connections" / "mysql",
            database_interaction_registry_path=resolved_data_dir / "database_interactions.json",
            react_pause_legacy_path=resolved_data_dir / "react_pause_sessions.json",
            skill_session_legacy_path=resolved_data_dir / "skill_sessions.json",
        )


def _resolve_workspace_root(workspace_root: str | Path | None) -> Path:
    raw = workspace_root or _env("WORKSPACE_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_app_home(app_home: str | Path | None) -> Path:
    raw = app_home or _env("APP_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / DEFAULT_APP_DIRNAME).resolve()


def _resolve_data_dir(data_dir: str | Path | None, app_home: Path) -> Path:
    raw = data_dir or _env("DATA_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return app_home


def _resolve_config_path(config_path: str | Path | None, data_dir: Path) -> Path:
    raw = config_path or _env("CONFIG")
    if raw:
        return Path(raw).expanduser().resolve()
    return (data_dir / "config.toml").resolve()


def _resolve_log_dir(log_dir: str | Path | None, data_dir: Path) -> Path:
    raw = log_dir or _env("LOG_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return (data_dir / "logs").resolve()


def _env(name: str) -> str | None:
    return os.getenv(f"SB_AGENT_{name}") or os.getenv(f"DAILY_AGENT_{name}")
