from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from daily_agent.app_context import ApplicationContext
from daily_agent.config import (
    DEFAULT_CONTEXT_TOKEN_BUDGET,
    get_config_path,
    load_config_file,
    load_settings,
    update_model_config,
    write_default_config,
)
from daily_agent.errors import DailyAgentError, MemoryError
from daily_agent.doctor import run_doctor
from daily_agent.skill_catalog import SkillCatalog
from daily_agent.tool_registry import ToolRegistry
from daily_agent.context import MySQLContextBuilder
from daily_agent.memory import DEFAULT_SESSION_ID, DEFAULT_USER_ID, ConversationMessage, ConversationSessionRecord, \
    MySQLMemoryStore
from daily_agent.model_provider import ModelConnectionTester, get_model_connection_tester
from daily_agent.planner import RuleBasedPlanner, TaskPlan
from daily_agent.skill_planner import SkillPlanner
from daily_agent.services.rendering import redact_config, sanitize_response_text


class PlanningService:
    """封装规则规划和 Markdown 保存逻辑。"""

    def __init__(
        self,
        planner: RuleBasedPlanner | None = None,
        skill_planner: SkillPlanner | None = None,
        app_context: ApplicationContext | None = None,
    ) -> None:
        self.app_context = app_context or ApplicationContext.build()
        self.planner = planner or RuleBasedPlanner()
        self.skill_planner = skill_planner or SkillPlanner(
            working_directory=str(self.app_context.paths.workspace_root),
        )

    def create_plan(self, query: str) -> TaskPlan:
        skill_plan = self.skill_planner.plan(query)
        if skill_plan:
            return skill_plan
        return self.planner.plan(query)

    def render_plan(self, plan: TaskPlan, query: str, output_format: str) -> str:
        if output_format == "markdown":
            return plan.to_markdown(request=query)
        return json.dumps(plan.to_metadata(), ensure_ascii=False, indent=2)

    def save_plan_markdown(self, plan: TaskPlan, query: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plan.to_markdown(request=query), encoding="utf-8")
        return path


class PreferenceService:
    def __init__(self, store: MySQLMemoryStore | None = None) -> None:
        self.store = store or MySQLMemoryStore()

    def set_preference(self, key: str, value: str, user_id: str = DEFAULT_USER_ID) -> None:
        self.store.set_preference(key, value, user_id=user_id)

    def list_preferences(self, user_id: str = DEFAULT_USER_ID):
        return self.store.list_preferences(user_id=user_id)

    def delete_preference(self, key: str, user_id: str = DEFAULT_USER_ID) -> bool:
        return self.store.delete_preference(key, user_id=user_id)


class HistoryService:
    def __init__(self, store: MySQLMemoryStore | None = None) -> None:
        self.store = store or MySQLMemoryStore()

    def list_history(self, kind: str = "tasks", user_id: str = DEFAULT_USER_ID, limit: int = 20):
        if kind == "tools":
            return self.store.list_tool_call_logs(user_id=user_id, limit=limit)
        return self.store.list_task_history(user_id=user_id, limit=limit)


class HealthService:
    def __init__(
        self,
        app_context: ApplicationContext | None = None,
        model_tester: ModelConnectionTester | None = None,
    ) -> None:
        self.app_context = app_context or ApplicationContext.build()
        self.model_tester = model_tester or get_model_connection_tester()

    def run_checks(self) -> list[dict[str, str]]:
        return run_doctor(paths=self.app_context.paths, model_tester=self.model_tester)


class ConversationService:
    """给工作台提供会话列表和消息历史。"""

    def __init__(
        self,
        store: MySQLMemoryStore | None = None,
        app_context: ApplicationContext | None = None,
    ) -> None:
        self.store = store or MySQLMemoryStore()
        self.app_context = app_context or ApplicationContext.build()

    def list_sessions(
        self,
        user_id: str = DEFAULT_USER_ID,
        limit: int = 20,
        offset: int = 0,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        if not self._table_available("conversation_messages"):
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        rows, total = self.store.list_conversation_sessions(
            user_id=user_id,
            limit=limit,
            offset=offset,
            keyword=keyword,
            date_from=date_from,
            date_to=date_to,
        )
        items = [
            ConversationSessionRecord(
                session_id=item.session_id,
                title=_sanitize_conversation_text(item.title),
                preview=_sanitize_conversation_text(item.preview),
                message_count=item.message_count,
                last_message_at=item.last_message_at,
                has_summary=item.has_summary,
            )
            for item in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_session_detail(
            self,
            user_id: str = DEFAULT_USER_ID,
            session_id: str = DEFAULT_SESSION_ID,
            limit: int = 100,
    ) -> dict:
        if not self._table_available("conversation_messages"):
            return {
                "session_id": session_id,
                "messages": [],
                "summary": None,
                "context_observation": None,
            }
        # 数据库读取默认是倒序，这里反转成正序，前端才能像聊天工具一样从上往下渲染。
        messages = list(
            reversed(self.store.list_conversation_messages(user_id=user_id, session_id=session_id, limit=limit)))
        summary = None
        if self._table_available("conversation_session_summaries"):
            summary = self.store.get_conversation_summary(user_id=user_id, session_id=session_id)
        context_observation = None
        try:
            settings = load_settings(paths=self.app_context.paths)
            context_observation = MySQLContextBuilder(store=self.store).build_observation(
                user_id=user_id,
                session_id=session_id,
                token_budget=settings.context_token_budget,
            )
        except (DailyAgentError, MemoryError, ValueError):
            context_observation = None
        return {
            "session_id": session_id,
            "messages": [_serialize_conversation_message(item) for item in messages],
            "summary": summary.__dict__ if summary else None,
            "context_observation": _serialize_context_observation(context_observation),
        }

    def _table_available(self, table_name: str) -> bool:
        """检查工作台依赖的会话表是否存在。

        这里故意把“表不存在”视为可恢复场景，而不是直接把接口打成 500。
        对个人自用来说，第一次打开工作台时数据库还没初始化很常见，
        这时返回空列表比整页报错更合理。
        """
        try:
            return self.store.table_exists(table_name)
        except (DailyAgentError, MemoryError):
            return False


class SkillCatalogService:
    """封装 skill 注册表，给 CLI / API / 工作台提供统一入口。"""

    def __init__(self, catalog: SkillCatalog | None = None, app_context: ApplicationContext | None = None) -> None:
        self.app_context = app_context or ApplicationContext.build()
        self.catalog = catalog or SkillCatalog(paths=self.app_context.paths)
        self.tool_registry = ToolRegistry(paths=self.app_context.paths, skill_catalog=self.catalog)

    def list_skills(self) -> list[dict[str, object]]:
        return [self._payload(item) for item in self.catalog.list_skills()]

    def get_skill_detail(self, name: str) -> dict[str, object] | None:
        item = next((record for record in self.catalog.list_skills() if record.name == name), None)
        if item is None:
            return None
        root = Path(item.path).expanduser().resolve()
        return {
            "skill": self._payload(item),
            "files": _read_skill_files(root),
            "persistence": {
                "definition_path": str(root),
                "status_registry_path": str(self.app_context.paths.skill_registry_path),
                "tool_registry_path": str(self.app_context.paths.capability_registry_path),
                "source": item.source,
            },
        }

    def import_skill(self, path: str, enable: bool = True, source_url: str | None = None) -> list[dict[str, object]]:
        source = (source_url or path or "").strip()
        return [item.to_dict() for item in self.catalog.import_source(source, enable=enable)]

    def enable_skill(self, name: str) -> dict[str, object]:
        return self.catalog.set_skill_status(name, "enabled").to_dict()

    def disable_skill(self, name: str) -> dict[str, object]:
        return self.catalog.set_skill_status(name, "disabled").to_dict()

    def adopt_skill(self, name: str) -> dict[str, object]:
        return self.catalog.adopt_workspace_skill(name).to_dict()

    def remove_skill(self, name: str) -> bool:
        return self.catalog.remove_skill(name)

    def _payload(self, item) -> dict[str, object]:
        return {**item.to_dict(), **self.tool_registry.skill_runtime_status(item)}


_SKILL_ROOT_FILES = ("SKILL.md", "skill.json", "manifest.json", ".skill.json")
_SKILL_TEXT_DIRECTORIES = ("scripts", "references")
_SKILL_ASSET_DIRECTORY = "assets"
_SKILL_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".mjs", ".ts", ".sh"}
_SKILL_FILE_LIMIT = 100
_SKILL_FILE_CHAR_LIMIT = 40_000
_SKILL_TOTAL_CHAR_LIMIT = 240_000


def _read_skill_files(root: Path) -> list[dict[str, object]]:
    """只读取 Skill 约定目录里的说明和源码，不扫描任意隐藏文件。"""
    if not root.is_dir():
        return []
    # 根目录只允许 Skill 标准文件；子目录也只接受 scripts、references、assets。
    # 这样即使 Skill 目录旁边存在 .env、SSH 配置等文件，详情接口也不会碰到它们。
    candidates: list[tuple[Path, str, bool]] = []
    for filename in _SKILL_ROOT_FILES:
        path = root / filename
        if path.is_file() and not path.is_symlink():
            candidates.append((path, "instructions" if filename == "SKILL.md" else "manifest", True))
    for directory_name in _SKILL_TEXT_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir() or directory.is_symlink():
            continue
        kind = "script" if directory_name == "scripts" else "reference"
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in _SKILL_TEXT_SUFFIXES:
                candidates.append((path, kind, True))
    assets_directory = root / _SKILL_ASSET_DIRECTORY
    if assets_directory.is_dir() and not assets_directory.is_symlink():
        for path in sorted(assets_directory.rglob("*")):
            if path.is_file() and not path.is_symlink():
                candidates.append((path, "asset", False))

    files: list[dict[str, object]] = []
    remaining_chars = _SKILL_TOTAL_CHAR_LIMIT
    # 单文件和总字符数都设上限，防止一个异常 Skill 把接口响应和浏览器内存撑满。
    for path, kind, readable in candidates[:_SKILL_FILE_LIMIT]:
        try:
            relative_path = path.resolve().relative_to(root).as_posix()
            size_bytes = path.stat().st_size
        except (OSError, ValueError):
            continue
        content = None
        truncated = False
        if readable and remaining_chars > 0:
            read_limit = min(_SKILL_FILE_CHAR_LIMIT, remaining_chars)
            try:
                with path.open("r", encoding="utf-8", errors="replace") as file:
                    content = file.read(read_limit + 1)
            except OSError:
                content = None
            if content is not None:
                truncated = len(content) > read_limit
                content = _redact_skill_text(content[:read_limit])
                remaining_chars -= len(content)
        files.append(
            {
                "path": relative_path,
                "kind": kind,
                "size_bytes": size_bytes,
                "content": content,
                "truncated": truncated,
            }
        )
    return files


def _redact_skill_text(content: str) -> str:
    """遮住误放进 Skill 文档或脚本里的常见密钥，避免详情接口直接回显。"""
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-***", content)
    redacted = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]{12,}", r"\1***", redacted)
    # 先处理带引号的 JSON/Python/JavaScript 写法，保留原来的键名和引号，方便用户定位问题。
    redacted = re.sub(
        r'''(?ix)(["']?(?:api[_-]?key|secret|password|authorization|access[_-]?token)["']?\s*[:=]\s*)
        (["'])(.*?)(\2)''',
        r"\1\2***\4",
        redacted,
    )
    # YAML、Shell 和 .properties 常见的是不带引号的值，只遮住当前行的值，避免误吞后续代码。
    redacted = re.sub(
        r'''(?im)^(\s*(?:api[_-]?key|secret|password|authorization|access[_-]?token)\s*[:=]\s*)([^\s#"'][^\r\n#]*)''',
        r"\1***",
        redacted,
    )
    return redacted


class ModelConfigService:
    """读取和更新模型配置。

    当前开放 model + context section，是因为工作台里最常改的就是：
    - 模型 ID
    - DashScope Key
    - DashScope Base URL
    - 上下文 token 预算
    """

    def __init__(
        self,
        app_context: ApplicationContext | None = None,
        model_tester: ModelConnectionTester | None = None,
    ) -> None:
        self.app_context = app_context or ApplicationContext.build()
        self.model_tester = model_tester or get_model_connection_tester()

    def get_model_config(self) -> dict:
        config_path = get_config_path(paths=self.app_context.paths)
        config = load_config_file(config_path, paths=self.app_context.paths)
        settings = load_settings(paths=self.app_context.paths)
        model_section = config.get("model", {})
        context_section = config.get("context", {})
        return {
            "config_path": str(config_path),
            "config_exists": config_path.exists(),
            "config": {
                "provider": str(model_section.get("provider", "openai_compatible")),
                "model_id": str(model_section.get("id", "deepseek-v4-flash")),
                "base_url": str(model_section.get("base_url", model_section.get("dashscope_base_url", "https://api.deepseek.com"))),
                "api_key_configured": bool(model_section.get("api_key", model_section.get("dashscope_api_key"))),
                "agno_telemetry": bool(model_section.get("agno_telemetry", False)),
                "thinking_enabled": bool(model_section.get("thinking_enabled", False)),
                "reasoning_effort": str(model_section.get("reasoning_effort", "high")),
                "context_token_budget": int(
                    context_section.get(
                        "token_budget",
                        context_section.get("char_budget", DEFAULT_CONTEXT_TOKEN_BUDGET),
                    )
                ),
            },
            "effective": {
                "provider": settings.model_provider,
                "model_id": settings.model_id,
                "base_url": settings.model_base_url,
                "api_key_configured": bool(settings.model_api_key),
                "agno_telemetry": settings.agno_telemetry,
                "thinking_enabled": settings.model_thinking_enabled,
                "reasoning_effort": settings.model_reasoning_effort,
                "context_token_budget": settings.context_token_budget,
            },
            "env_overrides": {
                "provider": _has_env("MODEL_PROVIDER"),
                "model_id": _has_env("MODEL"),
                "api_key": _model_env_override("MODEL_API_KEY", "DASHSCOPE_API_KEY"),
                "base_url": _model_env_override("MODEL_BASE_URL", "DASHSCOPE_BASE_URL"),
                "agno_telemetry": bool(_has_env("AGNO_TELEMETRY") or os.getenv("AGNO_TELEMETRY")),
                "thinking_enabled": _has_env("MODEL_THINKING_ENABLED"),
                "reasoning_effort": _has_env("MODEL_REASONING_EFFORT"),
                "context_token_budget": bool(
                    _has_env("CONTEXT_TOKEN_BUDGET") or _has_env("CONTEXT_CHAR_BUDGET")
                ),
            },
            "redacted_config": redact_config(config),
        }

    def save_model_config(
            self,
            *,
            provider: str,
            model_id: str,
            api_key: str,
            base_url: str,
            clear_api_key: bool,
            agno_telemetry: bool,
            thinking_enabled: bool = False,
            reasoning_effort: str = "high",
            context_token_budget: int,
    ) -> dict:
        config_path = self.app_context.paths.config_path
        current_config = load_config_file(config_path, paths=self.app_context.paths)
        current_model = current_config.get("model", {})
        current_key = str(current_model.get("api_key", current_model.get("dashscope_api_key", "")))
        saved_key = "" if clear_api_key else (api_key.strip() or current_key)
        # 如果配置文件还不存在，先按默认模板落一份，避免直接修改空文件路径时体验生硬。
        write_default_config(path=config_path, overwrite=False)
        update_model_config(
            model_provider=provider,
            model_id=model_id,
            model_api_key=saved_key,
            model_base_url=base_url,
            agno_telemetry=agno_telemetry,
            model_thinking_enabled=thinking_enabled,
            model_reasoning_effort=reasoning_effort,
            context_token_budget=context_token_budget,
            path=config_path,
        )
        self.model_tester.clear()
        return self.get_model_config()

    def test_model_connection(self) -> dict[str, object]:
        settings = load_settings(paths=self.app_context.paths)
        return self.model_tester.check(settings, force=True).to_dict()

    def list_available_models(self) -> dict[str, object]:
        """按当前生效配置请求 Provider 模型列表，供设置页下拉切换。"""
        from daily_agent.model_provider import fetch_available_models

        settings = load_settings(paths=self.app_context.paths)
        started = time.perf_counter()
        try:
            models = fetch_available_models(settings)
            return {
                "status": "ok",
                "models": models,
                "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
            }
        except Exception as exc:
            from daily_agent.model_provider import _model_error_message

            return {
                "status": "error",
                "models": [],
                "detail": _model_error_message(exc),
                "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
            }


def _has_env(name: str) -> bool:
    return bool(os.getenv(f"SB_AGENT_{name}") or os.getenv(f"DAILY_AGENT_{name}"))


def _model_env_override(primary: str, legacy: str) -> bool:
    return bool(_has_env(primary) or _has_env(legacy) or os.getenv(legacy))


def _sanitize_conversation_text(text: str | None) -> str:
    return sanitize_response_text(text or "")


def _serialize_conversation_message(message: ConversationMessage) -> dict:
    payload = message.__dict__.copy()
    if message.role == "assistant":
        payload["content"] = _sanitize_conversation_text(message.content)
    payload["attachment_ids"] = list(message.attachment_ids or ())
    payload.pop("attachments", None)
    return payload


def _serialize_context_observation(observation) -> dict | None:
    if not observation:
        return None
    return {
        "instructions": observation.instructions,
        "budget": dict(observation.budget),
        "compression": dict(observation.compression),
    }
