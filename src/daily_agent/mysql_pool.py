"""MySQL 连接池：复用 PyMySQL 连接，避免多用户并发时频繁握手。

Python 侧与 Java Druid 最接近的开箱即用方案是 DBUtils.PooledDB：
- 进程内维护连接池，借出/归还对业务代码透明；
- 支持 maxconnections、mincached、ping 保活；
- 与现有 pymysql + DictCursor 用法兼容，无需引入 SQLAlchemy。
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Any

from daily_agent.config import Settings, load_config_file
from daily_agent.errors import DependencyError, MemoryError

# 进程级池缓存：同一套 MySQL 配置只建一个 PooledDB 实例。
_POOLS: dict[str, Any] = {}
_POOLS_LOCK = threading.Lock()

# 与 Druid 默认量级接近的保守默认值；可在 config.toml [mysql] 或环境变量覆盖。
_DEFAULT_MAX_CONNECTIONS = 20
_DEFAULT_MIN_CACHED = 2
_DEFAULT_MAX_CACHED = 10


def borrow_connection(settings: Settings, *, database: str | None) -> Any:
    """从连接池借出一条连接；database 为 None 时走直连（仅 init_schema 等管理场景）。"""
    if database is None:
        return _connect_direct(settings, database=None)
    pool = _get_or_create_pool(settings, database=database)
    try:
        return pool.connection()
    except Exception as exc:
        raise MemoryError(f"MySQL 记忆库连接失败：{exc}") from exc


def _get_or_create_pool(settings: Settings, *, database: str) -> Any:
    pool_key = _pool_key(settings, database=database)
    # 热路径不再争用池创建锁；已发布的池实例在进程运行中不会替换。
    existing = _POOLS.get(pool_key)
    if existing is not None:
        return existing
    with _POOLS_LOCK:
        existing = _POOLS.get(pool_key)
        if existing is not None:
            return existing
        pool = _build_pool(settings, database=database)
        _POOLS[pool_key] = pool
        return pool


def _build_pool(settings: Settings, *, database: str) -> Any:
    try:
        import pymysql
        from dbutils.pooled_db import PooledDB
        from pymysql.cursors import DictCursor
    except ImportError as exc:
        raise DependencyError(
            "缺少 PyMySQL 或 DBUtils 依赖，请先运行 pip install ."
        ) from exc

    pool_max, pool_min, pool_cached = _resolve_pool_limits()
    return PooledDB(
        creator=pymysql,
        maxconnections=max(1, pool_max),
        mincached=max(0, pool_min),
        maxcached=max(0, pool_cached),
        # PyMySQL 连接不能跨线程共享同一条物理连接，保持 0。
        maxshared=0,
        # 容量耗尽立即返回业务错误，不让问答线程无限排队等连接。
        blocking=False,
        ping=1,
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password or "",
        database=database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        **_connection_timeouts(),
    )


def _connect_direct(settings: Settings, *, database: str | None) -> Any:
    """不走连接池的直连，仅用于建库/初始化等低频管理操作。"""
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:
        raise DependencyError("缺少 PyMySQL 依赖，请先运行 pip install .") from exc

    connect_kwargs: dict[str, Any] = {
        "host": settings.mysql_host,
        "port": settings.mysql_port,
        "user": settings.mysql_user,
        "password": settings.mysql_password or "",
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
        **_connection_timeouts(),
    }
    if database is not None:
        connect_kwargs["database"] = database
    try:
        return pymysql.connect(**connect_kwargs)
    except Exception as exc:
        raise MemoryError(f"MySQL 记忆库连接失败：{exc}") from exc


def _connection_timeouts() -> dict[str, int]:
    """约束应用数据库的网络等待；环境变量可按部署延迟调整。"""
    return {
        "connect_timeout": _as_positive_int(os.getenv("MYSQL_CONNECT_TIMEOUT_SECONDS"), default=5),
        "read_timeout": _as_positive_int(os.getenv("MYSQL_READ_TIMEOUT_SECONDS"), default=15),
        "write_timeout": _as_positive_int(os.getenv("MYSQL_WRITE_TIMEOUT_SECONDS"), default=15),
    }


def _pool_key(settings: Settings, *, database: str) -> str:
    raw = "|".join(
        [
            settings.mysql_host,
            str(settings.mysql_port),
            settings.mysql_user or "",
            settings.mysql_password or "",
            database,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_pool_limits() -> tuple[int, int, int]:
    """读取 pool 上限；环境变量优先于 config.toml [mysql]。"""
    import os

    mysql_config = load_config_file().get("mysql", {})
    if not isinstance(mysql_config, dict):
        mysql_config = {}

    max_connections = _as_positive_int(
        os.getenv("MYSQL_POOL_MAX_CONNECTIONS"),
        mysql_config.get("pool_max_connections"),
        default=_DEFAULT_MAX_CONNECTIONS,
    )
    min_cached = _as_positive_int(
        os.getenv("MYSQL_POOL_MIN_CACHED"),
        mysql_config.get("pool_min_cached"),
        default=_DEFAULT_MIN_CACHED,
    )
    max_cached = _as_positive_int(
        os.getenv("MYSQL_POOL_MAX_CACHED"),
        mysql_config.get("pool_max_cached"),
        default=_DEFAULT_MAX_CACHED,
    )
    max_cached = max(min_cached, max_cached)
    max_connections = max(max_cached, max_connections)
    return max_connections, min_cached, max_cached


def _as_positive_int(*candidates: object, default: int) -> int:
    for candidate in candidates:
        if candidate is None or candidate == "":
            continue
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return default


def reset_pools_for_tests() -> None:
    """测试专用：清空进程内池缓存。"""
    with _POOLS_LOCK:
        _POOLS.clear()
