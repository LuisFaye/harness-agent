from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from daily_agent.api import create_app
from daily_agent.app_context import ApplicationContext
from daily_agent.config import (
    Settings,
    default_config_dict,
    default_config_text,
    load_config_file,
    load_secret_value,
    load_settings,
    merge_config_with_defaults,
    save_config_file,
)
from daily_agent.doctor import run_doctor
from daily_agent.model_provider import ModelConnectionTester, build_model, fetch_available_models
from daily_agent.paths import AppPaths
from daily_agent.services.platform_service import ModelConfigService


def _settings(**overrides) -> Settings:
    values = {
        "model_provider": "openai_compatible",
        "model_id": "deepseek-chat",
        "model_api_key": "sk-secret",
        "model_base_url": "https://model.example/v1",
        "agno_telemetry": False,
        "context_token_budget": 4000,
        "mysql_host": "127.0.0.1",
        "mysql_port": 3306,
        "mysql_user": "root",
        "mysql_password": "password",
        "mysql_database": "sb_agent",
        "agent_max_tool_calls": 16,
        "agent_max_duration_seconds": 180,
    }
    values.update(overrides)
    return Settings(**values)


def test_local_secret_uses_private_config_and_environment_override(tmp_path, monkeypatch):
    paths = AppPaths.discover(data_dir=tmp_path)
    save_config_file({"secrets": {"MCP_TOKEN": "Bearer file-secret"}}, path=paths.config_path)

    assert load_secret_value("MCP_TOKEN", paths=paths) == "Bearer file-secret"
    assert paths.config_path.stat().st_mode & 0o777 == 0o600

    monkeypatch.setenv("MCP_TOKEN", "Bearer environment-secret")

    assert load_secret_value("MCP_TOKEN", paths=paths) == "Bearer environment-secret"


def test_legacy_dashscope_config_is_still_readable(tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path)
    paths.config_path.write_text(
        """[model]
id = "qwen-plus"
dashscope_api_key = "sk-legacy"
dashscope_base_url = "https://dashscope.example/v1"
""",
        encoding="utf-8",
    )

    settings = load_settings(paths=paths)

    assert settings.model_provider == "openai_compatible"
    assert settings.model_api_key == "sk-legacy"
    assert settings.model_base_url == "https://dashscope.example/v1"
    assert settings.agno_telemetry is False


def test_agent_budget_reads_from_agent_toml_section(tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path)
    paths.config_path.write_text(
        """[agent]
max_tool_calls = 24
max_duration_seconds = 300
""",
        encoding="utf-8",
    )

    settings = load_settings(paths=paths)

    assert settings.agent_max_tool_calls == 24
    assert settings.agent_max_duration_seconds == 300


def test_agent_budget_defaults_and_environment_precedence(tmp_path, monkeypatch):
    paths = AppPaths.discover(data_dir=tmp_path)
    paths.config_path.write_text(
        """[agent]
max_tool_calls = 24
max_duration_seconds = 300
""",
        encoding="utf-8",
    )

    settings = load_settings(paths=paths)

    assert settings.agent_max_tool_calls == 24
    assert settings.agent_max_duration_seconds == 300

    monkeypatch.setenv("SB_AGENT_MAX_TOOL_CALLS", "32")
    settings = load_settings(paths=paths)

    assert settings.agent_max_tool_calls == 32
    assert settings.agent_max_duration_seconds == 300

    monkeypatch.setenv("DAILY_AGENT_MAX_DURATION_SECONDS", "240")
    settings = load_settings(paths=paths)

    assert settings.agent_max_tool_calls == 32
    assert settings.agent_max_duration_seconds == 240


def test_agent_budget_defaults_without_config_file(tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path)

    settings = load_settings(paths=paths)

    assert settings.agent_max_tool_calls == 16
    assert settings.agent_max_duration_seconds == 0
    assert settings.agent_max_inline_tokens == 4000


def test_agent_budget_clamps_to_at_least_one(tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path)
    paths.config_path.write_text(
        """[agent]
max_tool_calls = 0
max_duration_seconds = -5
max_inline_tokens = 0
""",
        encoding="utf-8",
    )

    settings = load_settings(paths=paths)

    assert settings.agent_max_tool_calls == 1
    # 总时长上限允许为 0（等于关闭限制），负数不能变成“立刻超时”。
    assert settings.agent_max_duration_seconds == 0
    assert settings.agent_max_inline_tokens == 1


def test_agent_budget_invalid_number_fails_clearly(tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path)
    paths.config_path.write_text(
        """[agent]
max_tool_calls = "sixteen"
max_duration_seconds = 180
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_settings(paths=paths)


def test_default_config_includes_agent_section_in_stable_order():
    default_config = default_config_dict()

    assert default_config["agent"] == {
        "max_tool_calls": 16,
        "max_duration_seconds": 0,
        "max_inline_tokens": 4000,
    }
    # 文档内联上限在 evidence 与 config 两处定义，这里锁定它们不掉队。
    from daily_agent.evidence import DOCUMENT_INLINE_TOKEN_LIMIT

    assert default_config["agent"]["max_inline_tokens"] == DOCUMENT_INLINE_TOKEN_LIMIT
    assert "[agent]" in default_config_text()
    text = default_config_text()
    section_order = [text.index(f"[{name}]") for name in ("model", "context", "agent", "mysql", "logging")]
    assert section_order == sorted(section_order)


def test_saving_another_section_does_not_mask_legacy_model_key(tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path)
    paths.config_path.write_text(
        """[model]
id = "deepseek-chat"
dashscope_api_key = "sk-legacy"
dashscope_base_url = "https://deepseek.example/v1"
""",
        encoding="utf-8",
    )
    config = merge_config_with_defaults(load_config_file(paths=paths))
    config["secrets"] = {"MCP_TOKEN": "Bearer search-secret"}
    save_config_file(config, path=paths.config_path)

    settings = load_settings(paths=paths)

    assert settings.model_api_key == "sk-legacy"
    assert settings.model_base_url == "https://deepseek.example/v1"


def test_model_settings_never_return_plain_key_and_blank_save_preserves_it(tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path)
    paths.config_path.write_text(
        """[model]
provider = "openai_compatible"
id = "deepseek-chat"
api_key = "sk-never-return-this"
base_url = "https://model.example/v1"
agno_telemetry = false

[context]
token_budget = 4000
""",
        encoding="utf-8",
    )
    tester = ModelConnectionTester(probe=lambda settings, timeout: None)
    service = ModelConfigService(app_context=ApplicationContext.build(paths=paths), model_tester=tester)

    payload = service.get_model_config()

    assert "sk-never-return-this" not in json.dumps(payload, ensure_ascii=False)
    assert payload["config"]["api_key_configured"] is True

    service.save_model_config(
        provider="openai_compatible",
        model_id="deepseek-chat",
        api_key="",
        base_url="https://model.example/v1",
        clear_api_key=False,
        agno_telemetry=False,
        context_token_budget=5000,
    )

    assert load_settings(paths=paths).model_api_key == "sk-never-return-this"


def test_model_settings_can_explicitly_clear_key(tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path)
    paths.config_path.write_text(
        """[model]
provider = "openai_compatible"
id = "deepseek-chat"
api_key = "sk-delete-me"
base_url = "https://model.example/v1"
""",
        encoding="utf-8",
    )
    service = ModelConfigService(
        app_context=ApplicationContext.build(paths=paths),
        model_tester=ModelConnectionTester(probe=lambda settings, timeout: None),
    )

    service.save_model_config(
        provider="openai_compatible",
        model_id="deepseek-chat",
        api_key="",
        base_url="https://model.example/v1",
        clear_api_key=True,
        agno_telemetry=False,
        context_token_budget=4000,
    )

    assert load_settings(paths=paths).model_api_key == ""


def test_model_connection_tester_uses_cache_and_force_refresh():
    calls = []

    def probe(settings, timeout):
        calls.append((settings.model_id, timeout))

    tester = ModelConnectionTester(probe=probe, cache_seconds=60)

    first = tester.check(_settings())
    second = tester.check(_settings())
    third = tester.check(_settings(), force=True)

    assert first.status == second.status == third.status == "ok"
    assert len(calls) == 2


def test_openai_compatible_model_keeps_system_role_for_deepseek():
    model = build_model(_settings())

    assert model.role_map["system"] == "system"


def test_fetch_available_models_parses_openai_compatible_response(monkeypatch):
    request = httpx.Request("GET", "https://model.example/v1/models")
    response = httpx.Response(
        200,
        request=request,
        json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]},
    )

    def fake_get(url, **kwargs):
        assert url == "https://model.example/v1/models"
        return response

    monkeypatch.setattr(httpx, "get", fake_get)

    assert fetch_available_models(_settings()) == ["deepseek-chat", "deepseek-reasoner"]


def test_model_connection_tester_translates_auth_failure_without_leaking_key():
    request = httpx.Request("GET", "https://model.example/v1/models")
    response = httpx.Response(401, request=request)

    def fail_probe(settings, timeout):
        raise httpx.HTTPStatusError("contains sk-secret", request=request, response=response)

    result = ModelConnectionTester(probe=fail_probe).check(_settings())

    assert result.status == "error"
    assert result.detail == "模型鉴权失败（HTTP 401），请检查 API Key。"
    assert "sk-secret" not in result.detail


def test_health_top_level_status_reflects_error_and_warning():
    class HealthStub:
        def __init__(self, checks):
            self.checks = checks

        def run_checks(self):
            return self.checks

    error_app = create_app(
        health_service=HealthStub([{"name": "model", "status": "error", "detail": "鉴权失败"}])
    )
    warn_app = create_app(
        health_service=HealthStub([{"name": "config", "status": "warn", "detail": "配置缺失"}])
    )

    assert TestClient(error_app).get("/health").json()["status"] == "error"
    assert TestClient(warn_app).get("/health").json()["status"] == "degraded"


def test_model_settings_api_accepts_legacy_field_names():
    class ModelServiceStub:
        def __init__(self):
            self.saved = None

        def save_model_config(self, **kwargs):
            self.saved = kwargs
            return {"config": {}, "effective": {}}

    service = ModelServiceStub()
    app = create_app(model_config_service=service)

    response = TestClient(app).post(
        "/settings/model",
        json={
            "model_id": "qwen-plus",
            "dashscope_api_key": "sk-legacy",
            "dashscope_base_url": "https://dashscope.example/v1",
        },
    )

    assert response.status_code == 200
    assert service.saved["api_key"] == "sk-legacy"
    assert service.saved["base_url"] == "https://dashscope.example/v1"


def test_doctor_checks_run_tables_and_reports_telemetry(monkeypatch, tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path)
    paths.config_path.write_text(
        """[model]
provider = "openai_compatible"
id = "deepseek-chat"
api_key = "sk-test"
base_url = "https://model.example/v1"
agno_telemetry = false
""",
        encoding="utf-8",
    )
    checked_tables = []

    class StoreStub:
        def __init__(self, settings):
            self.settings = settings

        def table_exists(self, table_name):
            checked_tables.append(table_name)
            return True

    monkeypatch.setattr("daily_agent.doctor.MySQLMemoryStore", StoreStub)

    checks = run_doctor(
        paths=paths,
        model_tester=ModelConnectionTester(probe=lambda settings, timeout: None),
    )

    assert "agent_runs" in checked_tables
    assert "agent_run_events" in checked_tables
    assert next(item for item in checks if item["name"] == "model")["status"] == "ok"
    assert next(item for item in checks if item["name"] == "agno_telemetry") == {
        "name": "agno_telemetry",
        "status": "ok",
        "detail": "Agno 遥测已关闭。",
    }
