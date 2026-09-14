import logging

from daily_agent.config import load_logging_settings
from daily_agent.logging_setup import bind_log_context, reset_logging_state, setup_logging
from daily_agent.paths import AppPaths


def test_setup_logging_creates_log_dir_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("SB_AGENT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SB_AGENT_LOG_LEVEL", "INFO")
    reset_logging_state()

    first_path = setup_logging(process_role="api")
    second_path = setup_logging(process_role="api")

    handlers = [handler for handler in logging.getLogger().handlers if (handler.get_name() or "").startswith("daily_agent")]
    file_handlers = [handler for handler in handlers if handler.get_name().startswith("daily_agent_file_")]

    assert first_path == second_path
    assert first_path.parent.exists()
    assert len(file_handlers) == 1


def test_setup_logging_writes_context_to_role_specific_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SB_AGENT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SB_AGENT_LOG_LEVEL", "INFO")
    reset_logging_state()

    log_path = setup_logging(process_role="worker")
    bind_log_context(process_role="worker", run_id="ask-1", user_id="u1", session_id="s1")
    logger = logging.getLogger("daily_agent.test")
    logger.info("hello log")

    for handler in logging.getLogger().handlers:
        handler.flush()

    text = log_path.read_text(encoding="utf-8")
    assert "role=worker" in text
    assert "run_id=ask-1" in text
    assert "user_id=u1" in text
    assert "session_id=s1" in text
    assert "hello log" in text


def test_logging_config_relative_dir_resolves_under_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("SB_AGENT_LOG_DIR", raising=False)
    monkeypatch.delenv("DAILY_AGENT_LOG_DIR", raising=False)
    paths = AppPaths.discover(
        app_home=tmp_path / "app-home",
        data_dir=tmp_path / "data",
        config_path=tmp_path / "data" / "config.toml",
        workspace_root=tmp_path / "workspace",
    )
    paths.config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text("[logging]\ndir = \"logs\"\n", encoding="utf-8")

    settings = load_logging_settings(paths=paths)

    assert settings.directory == paths.data_dir / "logs"
