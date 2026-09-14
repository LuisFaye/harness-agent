from __future__ import annotations

from typing import Callable

from daily_agent.config import Settings, load_settings
from daily_agent.memory import MySQLMemoryStore
from daily_agent.paths import AppPaths

StoreFactory = Callable[[], MySQLMemoryStore]


def build_store_factory(*, settings: Settings | None = None, paths: AppPaths | None = None) -> StoreFactory:
    """统一创建 MySQL store factory，避免业务层到处直接 new。"""
    resolved_settings = settings or load_settings(paths=paths)

    def factory() -> MySQLMemoryStore:
        return MySQLMemoryStore(settings=resolved_settings)

    return factory
