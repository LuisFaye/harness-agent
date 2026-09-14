from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from daily_agent.database_connections import (
    DatabaseConnectionCatalog,
    DatabaseConnectionService,
    DatabaseInteractionStore,
    classify_mysql_sql,
    sanitize_database_credentials,
)
from daily_agent.database_models import DatabaseConnectionWriteRequest
from daily_agent.errors import DailyAgentError
from daily_agent.mcp_catalog import McpCatalog, McpServerRecord
from daily_agent.paths import AppPaths


def _paths(tmp_path: Path) -> AppPaths:
    return AppPaths.discover(
        app_home=tmp_path,
        workspace_root=tmp_path,
        data_dir=tmp_path,
        config_path=tmp_path / "config.toml",
        log_dir=tmp_path / "logs",
    )


def _request(password: str = "secret-value") -> DatabaseConnectionWriteRequest:
    return DatabaseConnectionWriteRequest(
        name="测试库",
        host="127.0.0.1",
        port=3306,
        database="agent_test",
        username="tester",
        password=password,
    )


def test_managed_connection_keeps_password_only_in_private_envfile(tmp_path):
    catalog = DatabaseConnectionCatalog(paths=_paths(tmp_path))
    record = catalog.create_managed(_request(), mcp_server_id="mcp-test")

    registry_text = catalog.path.read_text(encoding="utf-8")
    envfile = Path(record.envfile_path)

    assert "secret-value" not in registry_text
    assert 'MYSQL_PASSWORD="secret-value"' in envfile.read_text(encoding="utf-8")
    assert stat.S_IMODE(envfile.stat().st_mode) == 0o600
    assert stat.S_IMODE(catalog.path.stat().st_mode) == 0o600


def test_update_without_password_preserves_existing_secret(tmp_path):
    catalog = DatabaseConnectionCatalog(paths=_paths(tmp_path))
    record = catalog.create_managed(_request(), mcp_server_id="mcp-test")

    updated = catalog.update(
        record.connection_id,
        _request(password="").model_copy(update={"host": "db.internal"}),
    )

    assert updated is not None
    assert updated.host == "db.internal"
    assert 'MYSQL_PASSWORD="secret-value"' in Path(record.envfile_path).read_text(encoding="utf-8")


def test_envfile_round_trips_complex_password(tmp_path):
    catalog = DatabaseConnectionCatalog(paths=_paths(tmp_path))
    record = catalog.create_managed(_request(password='space # quote " value'), mcp_server_id="mcp-test")

    updated = catalog.update(record.connection_id, _request(password=""))

    assert updated is not None
    env_text = Path(record.envfile_path).read_text(encoding="utf-8")
    assert 'MYSQL_PASSWORD="space # quote \\" value"' in env_text


def test_external_envfile_is_adopted_and_preserved_on_delete(tmp_path):
    envfile = tmp_path / "legacy.env"
    envfile.write_text(
        "\n".join(
            [
                "MYSQL_HOST=legacy-host",
                "MYSQL_PORT=4306",
                "MYSQL_USER=legacy-user",
                "MYSQL_PASSWORD=legacy-secret",
                "MYSQL_DATABASE=legacy-db",
            ]
        ),
        encoding="utf-8",
    )
    catalog = DatabaseConnectionCatalog(paths=_paths(tmp_path))
    record = catalog.adopt_external(
        server=McpServerRecord(
            server_id="mcp-legacy",
            name="mysql-front",
            transport_type="stdio",
            command="mysql_mcp_server_pro",
        ),
        envfile=envfile,
    )

    assert record.imported is True
    assert record.host == "legacy-host"
    assert "legacy-secret" not in catalog.path.read_text(encoding="utf-8")

    catalog.delete(record.connection_id)

    assert envfile.exists()


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", "read"),
        ("SHOW TABLES", "read"),
        ("DESCRIBE users", "read"),
        ("EXPLAIN SELECT * FROM users", "read"),
        ("WITH recent AS (SELECT * FROM users) SELECT * FROM recent", "read"),
        ("INSERT INTO users(id) VALUES (1)", "write"),
        ("UPDATE users SET name = 'x'", "write"),
        ("DELETE FROM users", "write"),
        ("CREATE TABLE demo(id INT)", "write"),
        ("SELECT * FROM users FOR UPDATE", "write"),
    ],
)
def test_mysql_sql_policy_uses_ast(sql, expected):
    assert classify_mysql_sql(sql) == expected


def test_mysql_sql_policy_rejects_multiple_statements():
    with pytest.raises(DailyAgentError, match="只能执行一条"):
        classify_mysql_sql("SELECT 1; SELECT 2")


def test_health_check_alias_does_not_use_mysql_reserved_current_user():
    source = Path("src/daily_agent/database_connections.py").read_text(encoding="utf-8")

    assert "AS authenticated_user" in source
    assert "AS current_user" not in source


def test_database_credentials_are_removed_before_model_and_history():
    sanitized, fields = sanitize_database_credentials(
        "连接 mysql://agent:plain-secret@db.local:3307/app 后查询，password=second-secret"
    )

    assert "plain-secret" not in sanitized
    assert "second-secret" not in sanitized
    assert fields == {
        "host": "db.local",
        "port": 3307,
        "username": "agent",
        "database": "app",
    }


def test_interaction_store_contains_no_password_and_consumes_once(tmp_path):
    store = DatabaseInteractionStore(paths=_paths(tmp_path))
    interaction = store.create(
        user_id="default",
        session_id="session-1",
        query="连接数据库后继续分析",
        fields={"host": "db.local", "username": "agent"},
    )
    payload = json.loads(store.path.read_text(encoding="utf-8"))

    assert "password" not in json.dumps(payload)
    assert store.consume(interaction.request_id) == "连接数据库后继续分析"
    assert store.consume(interaction.request_id) is None


def test_failed_new_connection_rolls_back_metadata_secret_and_mcp(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    catalog = DatabaseConnectionCatalog(paths=paths)
    mcp_catalog = McpCatalog(path=paths.mcp_registry_path)

    class FailingRuntime:
        def connect(self, record, force=False):
            raise DailyAgentError("连接失败")

        def disconnect(self, server_id):
            return True

    monkeypatch.setattr("daily_agent.database_connections.shutil.which", lambda command: "/usr/local/bin/mysql_mcp_server_pro")
    service = DatabaseConnectionService(
        catalog=catalog,
        mcp_catalog=mcp_catalog,
        runtime=FailingRuntime(),
        interaction_store=DatabaseInteractionStore(paths=paths),
    )

    with pytest.raises(DailyAgentError, match="连接失败"):
        service.create(_request())

    assert catalog.list_connections() == []
    assert mcp_catalog.list_servers() == []
    assert list(paths.database_connection_home.glob("*.env")) == []
