from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from daily_agent.paths import AppPaths, DEFAULT_APP_DIRNAME

DEFAULT_CONFIG_PATH = Path.home() / DEFAULT_APP_DIRNAME / "config.toml"
DEFAULT_LOG_DIR = Path.home() / DEFAULT_APP_DIRNAME / "logs"
DEFAULT_CONTEXT_TOKEN_BUDGET = 1_000_000
DEFAULT_API_PORT = 16666


@dataclass(frozen=True)
class Settings:
    model_provider: str
    model_id: str
    model_api_key: str | None
    model_base_url: str
    agno_telemetry: bool
    context_token_budget: int
    mysql_host: str
    mysql_port: int
    mysql_user: str | None
    mysql_password: str | None
    mysql_database: str
    model_thinking_enabled: bool = False
    model_reasoning_effort: str = "high"
    agent_max_tool_calls: int = 16
    agent_max_duration_seconds: int = 0
    # 网页/文件正文内联进上下文的 token 上限；超过才落证据库、只回预览。
    # 调大可减少“抓一次再读一次”的工具轮次，但也会增加当前请求输入量；
    # 实际 token、缓存命中和首字延迟应结合运行指标评估。
    agent_max_inline_tokens: int = 4000

    @property
    def dashscope_api_key(self) -> str | None:
        """兼容旧调用方，后续业务代码统一改用 model_api_key。"""
        return self.model_api_key

    @property
    def dashscope_base_url(self) -> str:
        """兼容旧调用方，后续业务代码统一改用 model_base_url。"""
        return self.model_base_url


@dataclass(frozen=True)
class LoggingSettings:
    level: str
    directory: Path


def load_settings(*, paths: AppPaths | None = None) -> Settings:
    config = load_config_file(paths=paths)
    model_config = config.get("model", {})
    context_config = config.get("context", {})
    mysql_config = config.get("mysql", {})
    agent_config = config.get("agent", {})

    return Settings(
        model_provider=str(_env("MODEL_PROVIDER", model_config.get("provider", "openai_compatible"))).strip(),
        model_id=_env("MODEL", str(model_config.get("id", "deepseek-v4-flash"))),
        model_api_key=_env(
            "MODEL_API_KEY",
            _env(
                "DASHSCOPE_API_KEY",
                os.getenv(
                    "DASHSCOPE_API_KEY",
                    _legacy_aware_model_value(model_config, "api_key", "dashscope_api_key", default=""),
                ),
            ),
        ),
        model_base_url=_env(
            "MODEL_BASE_URL",
            _env(
                "DASHSCOPE_BASE_URL",
                os.getenv(
                    "DASHSCOPE_BASE_URL",
                    _legacy_aware_model_value(
                        model_config,
                        "base_url",
                        "dashscope_base_url",
                        default="https://api.deepseek.com",
                    ),
                ),
            ),
        ),
        agno_telemetry=_as_bool(
            _env("AGNO_TELEMETRY", os.getenv("AGNO_TELEMETRY", model_config.get("agno_telemetry", False)))
        ),
        model_thinking_enabled=_as_bool(
            _env("MODEL_THINKING_ENABLED", model_config.get("thinking_enabled", False))
        ),
        model_reasoning_effort=_reasoning_effort(
            _env("MODEL_REASONING_EFFORT", model_config.get("reasoning_effort", "high"))
        ),
        context_token_budget=int(
            _env(
                "CONTEXT_TOKEN_BUDGET",
                _env(
                    "CONTEXT_CHAR_BUDGET",
                    str(
                        context_config.get(
                            "token_budget",
                            context_config.get("char_budget", DEFAULT_CONTEXT_TOKEN_BUDGET),
                        )
                    ),
                ),
            )
        ),
        agent_max_tool_calls=_as_min_one_int(
            _env("MAX_TOOL_CALLS", agent_config.get("max_tool_calls", 16))
        ),
        agent_max_duration_seconds=_as_agent_duration_seconds(
            _env("MAX_DURATION_SECONDS", agent_config.get("max_duration_seconds", 0))
        ),
        agent_max_inline_tokens=_as_min_one_int(
            _env("MAX_INLINE_TOKENS", agent_config.get("max_inline_tokens", 4000))
        ),
        mysql_host=_env("MYSQL_HOST", str(mysql_config.get("host", "127.0.0.1"))),
        mysql_port=int(_env("MYSQL_PORT", str(mysql_config.get("port", 3306)))),
        mysql_user=_env("MYSQL_USER", mysql_config.get("user")),
        mysql_password=_env("MYSQL_PASSWORD", mysql_config.get("password")),
        mysql_database=_env("MYSQL_DATABASE", str(mysql_config.get("database", "sb_agent"))),
    )


def load_logging_settings(*, paths: AppPaths | None = None) -> LoggingSettings:
    config = load_config_file(paths=paths)
    logging_config = config.get("logging", {})
    raw_directory = _env("LOG_DIR", logging_config.get("dir"))
    return LoggingSettings(
        level=str(_env("LOG_LEVEL", logging_config.get("level", "INFO"))),
        directory=_resolve_logging_directory(raw_directory, paths=paths),
    )


def load_config_file(path: Path | None = None, *, paths: AppPaths | None = None) -> dict[str, Any]:
    config_path = path or get_config_path(paths=paths)
    if not config_path.exists():
        return {}
    with config_path.open("rb") as file:
        return tomllib.load(file)


def load_secret_value(name: str, *, paths: AppPaths | None = None) -> str | None:
    """读取只在本机使用的运行时密钥，环境变量优先于配置文件。"""
    environment_value = os.getenv(name)
    if environment_value:
        return environment_value
    secrets = load_config_file(paths=paths).get("secrets", {})
    if not isinstance(secrets, dict):
        return None
    value = str(secrets.get(name) or "").strip()
    return value or None


def default_config_dict() -> dict[str, Any]:
    """把默认 TOML 文本解析成字典。

    这样后续无论是生成配置文件，还是在现有配置上补缺省字段，
    都能基于同一份默认结构，避免默认值散落在多处。
    """
    return tomllib.loads(default_config_text())


def get_config_path(*, paths: AppPaths | None = None) -> Path:
    configured_path = _env("CONFIG")
    if configured_path:
        return Path(configured_path).expanduser()
    if paths is not None:
        return paths.config_path
    return DEFAULT_CONFIG_PATH


def write_default_config(path: Path | None = None, overwrite: bool = False) -> Path:
    config_path = path or get_config_path()
    if config_path.exists() and not overwrite:
        return config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(default_config_text(), encoding="utf-8")
    config_path.chmod(0o600)
    return config_path


def save_config_file(config: dict[str, Any], path: Path | None = None) -> Path:
    """把配置字典写回 TOML 文件。

    当前项目的配置结构比较固定，主要是几组扁平 section，
    所以这里不额外引入 TOML 写入依赖，而是用受控渲染方式输出。
    这能满足本地自用场景，也方便 API 直接更新模型配置。
    """
    config_path = path or get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(render_config_text(config), encoding="utf-8")
    # 配置中可能包含模型、数据库和 MCP 密钥，只允许当前系统用户读取。
    config_path.chmod(0o600)
    return config_path


def merge_config_with_defaults(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """把已有配置合并到默认配置结构里。

    这里做的是“按 section 浅合并”：
    - 先拿默认配置保证字段完整；
    - 再用用户当前配置覆盖同名字段；
    - 未知 section 也保留，避免覆盖掉后续新增或手工扩展的内容。
    """
    merged = default_config_dict()
    for section, value in (config or {}).items():
        if isinstance(value, dict):
            target = merged.setdefault(section, {})
            if isinstance(target, dict):
                target.update(value)
            else:
                merged[section] = dict(value)
        else:
            merged[section] = value
    return merged


def update_model_config(
    *,
    model_provider: str,
    model_id: str,
    model_api_key: str,
    model_base_url: str,
    agno_telemetry: bool,
    model_thinking_enabled: bool = False,
    model_reasoning_effort: str = "high",
    context_token_budget: int | None = None,
    path: Path | None = None,
) -> Path:
    """更新 model/context section，并把结果写回配置文件。"""
    config = merge_config_with_defaults(load_config_file(path))
    config["model"] = {
        "provider": model_provider.strip() or "openai_compatible",
        "id": model_id.strip() or "deepseek-v4-flash",
        "api_key": model_api_key,
        "base_url": model_base_url.strip() or "https://api.deepseek.com",
        "agno_telemetry": bool(agno_telemetry),
        "thinking_enabled": bool(model_thinking_enabled),
        "reasoning_effort": _reasoning_effort(model_reasoning_effort),
    }
    if context_token_budget is not None:
        context_section = dict(config.get("context", {}))
        context_section["token_budget"] = max(1, int(context_token_budget))
        context_section.pop("char_budget", None)
        config["context"] = context_section
    return save_config_file(config, path=path)


def render_config_text(config: dict[str, Any]) -> str:
    """把配置字典渲染成 TOML 文本。

    目前只需要支持当前项目里用到的基础标量类型：
    字符串、整数、布尔值。后续如果配置结构扩大，再继续扩展即可。
    """
    lines = [
        "# SB Agent 本地配置",
        "# 这个文件包含密钥和数据库密码，已被 .gitignore 忽略，不要提交。",
        "",
    ]
    remaining = dict(config)
    ordered_sections = ["model", "context", "agent", "mysql", "logging"]
    for section in ordered_sections:
        value = remaining.pop(section, None)
        if isinstance(value, dict):
            lines.extend(_render_toml_section(section, value))

    for section, value in remaining.items():
        if isinstance(value, dict):
            lines.extend(_render_toml_section(section, value))

    return "\n".join(lines).rstrip() + "\n"


def _render_toml_section(name: str, values: dict[str, Any]) -> list[str]:
    """把一个 section 渲染成 TOML 片段。"""
    lines = [f"[{name}]"]
    for key, value in values.items():
        lines.append(f"{key} = {_render_toml_value(value)}")
    lines.append("")
    return lines


def _render_toml_value(value: Any) -> str:
    """把 Python 标量转换成 TOML 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = "" if value is None else str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _resolve_logging_directory(raw_directory: Any | None, *, paths: AppPaths | None = None) -> Path:
    """解析日志目录。

    配置文件里的相对路径按应用数据目录解析，避免从不同工作目录启动时，
    日志一会儿落在项目里、一会儿落在用户目录里。
    """
    if raw_directory is None or str(raw_directory).strip() == "":
        return paths.log_dir if paths else DEFAULT_LOG_DIR
    directory = Path(str(raw_directory)).expanduser()
    if directory.is_absolute():
        return directory
    base_dir = paths.data_dir if paths else DEFAULT_CONFIG_PATH.parent
    return (base_dir / directory).resolve()


def default_config_text() -> str:
    return """# SB Agent 本地配置
# 这个文件包含密钥和数据库密码，已被 .gitignore 忽略，不要提交。

[model]
provider = "openai_compatible"
id = "deepseek-v4-flash"
api_key = ""
base_url = "https://api.deepseek.com"
agno_telemetry = false
thinking_enabled = false
reasoning_effort = "high"

[context]
token_budget = 1000000

[agent]
max_tool_calls = 16
max_duration_seconds = 0
# 网页/文件正文内联上限（token）。搜索结果等噪声型结果不受这个值影响，
# 仍使用较小的通用阈值走证据压缩。
max_inline_tokens = 4000

[mysql]
host = "127.0.0.1"
port = 3306
user = "root"
# 请填写部署环境自己的 MySQL 密码，不要把真实密码提交到仓库。
password = ""
database = "sb_agent"
pool_max_connections = 20
pool_min_cached = 2
pool_max_cached = 10

[logging]
level = "INFO"
dir = "logs"
"""


def _legacy_aware_model_value(
    model_config: dict[str, Any],
    canonical_key: str,
    legacy_key: str,
    *,
    default: str,
) -> str:
    """兼容旧字段，同时保留用户明确清空新字段的语义。"""
    canonical = model_config.get(canonical_key)
    legacy = model_config.get(legacy_key)
    if legacy and (canonical in (None, "", default)):
        return str(legacy)
    if canonical is not None:
        return str(canonical)
    if legacy is not None:
        return str(legacy)
    return default


def _env(name: str, default: Any | None = None) -> Any:
    value = os.getenv(f"SB_AGENT_{name}")
    if value is not None:
        return value
    legacy_value = os.getenv(f"DAILY_AGENT_{name}")
    if legacy_value is not None:
        return legacy_value
    return default


def _as_bool(value: Any) -> bool:
    """把 TOML、环境变量中的常见真假写法统一成布尔值。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_min_one_int(value: Any) -> int:
    """把 Agent 执行预算解析成至少为 1 的整数。

    非数字输入由 int() 直接抛错，和 context_token_budget、mysql_port 等
    其他运行时数字配置的失败方式保持一致。
    """
    return max(1, int(value))


def _as_agent_duration_seconds(value: Any) -> int:
    """0 表示不限总时长；负数同样归零，单工具和模型空闲期限仍独立生效。"""
    return max(0, int(value))


def _reasoning_effort(value: Any) -> str:
    normalized = str(value or "high").strip().lower()
    return normalized if normalized in {"high", "max"} else "high"


def api_port() -> int:
    """解析 API 监听端口，支持 SB_AGENT_API_PORT / DAILY_AGENT_API_PORT 环境变量覆盖。"""
    return int(_env("API_PORT", str(DEFAULT_API_PORT)))
