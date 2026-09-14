from __future__ import annotations

from daily_agent.config import Settings, get_config_path, load_settings
from daily_agent.memory import MySQLMemoryStore
from daily_agent.model_provider import ModelConnectionTester, get_model_connection_tester
from daily_agent.paths import AppPaths


def run_doctor(
    *,
    paths: AppPaths | None = None,
    model_tester: ModelConnectionTester | None = None,
) -> list[dict[str, str]]:
    resolved_paths = paths or AppPaths.discover()
    settings = load_settings(paths=resolved_paths)
    checks = [
        _check_paths(resolved_paths),
        _check_config_file(resolved_paths),
        _check_model(settings, model_tester or get_model_connection_tester()),
        _check_mysql(settings),
        _check_agno_telemetry(settings),
    ]
    return checks


def _check_paths(paths: AppPaths) -> dict[str, str]:
    return {
        "name": "paths",
        "status": "ok",
        "detail": (
            f"app_home={paths.app_home}；workspace_root={paths.workspace_root}；"
            f" data_dir={paths.data_dir}；log_dir={paths.log_dir}"
        ),
    }


def _check_config_file(paths: AppPaths) -> dict[str, str]:
    path = get_config_path(paths=paths)
    if path.exists():
        return {"name": "config", "status": "ok", "detail": f"配置文件存在：{path}"}
    return {"name": "config", "status": "warn", "detail": f"配置文件不存在：{path}"}


def _check_model(settings: Settings, tester: ModelConnectionTester) -> dict[str, str]:
    result = tester.check(settings)
    return {"name": "model", "status": result.status, "detail": result.detail}


def _check_mysql(settings: Settings) -> dict[str, str]:
    try:
        store = MySQLMemoryStore(settings=settings)
        required_tables = [
            "user_preferences",
            "conversation_memories",
            "conversation_messages",
            "conversation_session_summaries",
            "conversation_session_titles",
            "task_history",
            "tool_call_logs",
            "react_pause_states",
            "skill_session_states",
            "agent_runs",
            "agent_run_events",
            "agent_run_evidence",
        ]
        missing = [table for table in required_tables if not store.table_exists(table)]
        if missing:
            return {
                "name": "mysql",
                "status": "warn",
                "detail": f"MySQL 可连接，但缺少表：{', '.join(missing)}。请运行 sb-agent memory init。",
            }
        return {"name": "mysql", "status": "ok", "detail": "MySQL 可连接，记忆表完整。"}
    except Exception as exc:
        return {"name": "mysql", "status": "error", "detail": str(exc)}


def _check_agno_telemetry(settings: Settings) -> dict[str, str]:
    if settings.agno_telemetry:
        return {
            "name": "agno_telemetry",
            "status": "warn",
            "detail": "Agno 遥测已开启，运行元数据可能发送到 Agno 服务。",
        }
    return {"name": "agno_telemetry", "status": "ok", "detail": "Agno 遥测已关闭。"}
