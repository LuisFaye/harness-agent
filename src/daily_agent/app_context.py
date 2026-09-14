from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from daily_agent.config import LoggingSettings, Settings, load_logging_settings, load_settings
from daily_agent.factories import StoreFactory, build_store_factory
from daily_agent.paths import AppPaths
from daily_agent.skill_catalog import SkillCatalog


@dataclass(frozen=True)
class ApplicationContext:
    """把启动时就能确定的核心依赖收口。"""

    paths: AppPaths
    settings: Settings
    logging_settings: LoggingSettings

    @classmethod
    def build(cls, *, paths: AppPaths | None = None) -> "ApplicationContext":
        resolved_paths = paths or AppPaths.discover()
        return cls(
            paths=resolved_paths,
            settings=load_settings(paths=resolved_paths),
            logging_settings=load_logging_settings(paths=resolved_paths),
        )

    def create_skill_catalog(self) -> SkillCatalog:
        return SkillCatalog(
            imported_home=self.paths.imported_skills_home,
            registry_path=self.paths.skill_registry_path,
        )

    def create_store_factory(self) -> StoreFactory:
        return build_store_factory(settings=self.settings, paths=self.paths)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "app_home": str(self.paths.app_home),
            "workspace_root": str(self.paths.workspace_root),
            "data_dir": str(self.paths.data_dir),
            "config_path": str(self.paths.config_path),
            "log_dir": str(self.paths.log_dir),
            "skill_registry_path": str(self.paths.skill_registry_path),
        }
