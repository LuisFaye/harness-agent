from __future__ import annotations

import re
import shlex
import shutil
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError

COMPAT_NATIVE = "native"
COMPAT_COMPATIBLE = "compatible"
COMPAT_DOCUMENT_ONLY = "document_only"
COMPAT_UNSUPPORTED = "unsupported"
COMPAT_ERROR = "error"

CAPABILITY_SHELL = "shell"
CAPABILITY_HTTP = "http"
CAPABILITY_BROWSER = "browser"
CAPABILITY_WEB_SEARCH = "web_search"
CAPABILITY_WEB_FETCH = "web_fetch"

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

COMPATIBLE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": "要交给这个兼容 skill 完成的自然语言任务。",
        },
        "url": {
            "type": "string",
            "description": "可选的网址。http/web_fetch/browser 类 skill 会优先使用它。",
        },
        "inputs": {
            "type": "object",
            "description": "可选的结构化输入，例如 command、method、headers、data 等。",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "单次执行最长等待秒数，默认 60 秒，最大 300 秒。",
        },
    },
    "required": [],
}

SAFE_SHELL_PREFIXES = ("node", "npx", "agent-browser", "curl")
AGENT_BROWSER_COMMANDS = {
    "auth",
    "back",
    "check",
    "click",
    "clipboard",
    "close",
    "connect",
    "console",
    "cookies",
    "dblclick",
    "dialog",
    "diff",
    "download",
    "drag",
    "errors",
    "eval",
    "fill",
    "find",
    "focus",
    "forward",
    "frame",
    "get",
    "highlight",
    "hover",
    "inspect",
    "is",
    "keyboard",
    "mouse",
    "network",
    "open",
    "pdf",
    "press",
    "profiler",
    "pushstate",
    "react",
    "record",
    "reload",
    "screenshot",
    "scroll",
    "scrollintoview",
    "select",
    "set",
    "skills",
    "snapshot",
    "state",
    "storage",
    "stream",
    "tab",
    "trace",
    "type",
    "uncheck",
    "upload",
    "vitals",
    "wait",
}

_BASH_TOOL_RE = re.compile(r"\bBash\s*\(([^)]*)\)", re.IGNORECASE | re.DOTALL)
_SHELL_PATTERN_RE = re.compile(
    r"(?m)^\s*(curl\s+https?://[^\s`#]+|node\s+[^\n`#]+|npx\s+[^\n`#]+|agent-browser(?:\s+[^\n`#]+)?)"
)
_COMMAND_FENCE_RE = re.compile(r"```(?:bash|sh|shell)?\s*\n(?P<body>.*?)```", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"(?:^|/)(scripts/[^\s`]+\.(?:mjs|js|cjs|py|sh))")


def analyze_skill_compatibility(
    *,
    folder: Path,
    manifest: dict[str, Any],
    frontmatter: dict[str, Any],
    content: str,
    source: str,
    install_source: str | None,
    name: str,
    description: str,
) -> dict[str, Any]:
    """把 native manifest 和第三方 SKILL.md 统一分析成运行时可消费的兼容元数据。"""
    diagnostics: list[str] = []
    capabilities = _capabilities_from_declared_tools(frontmatter, manifest)
    capabilities.update(_capabilities_from_content(content))

    execution = manifest.get("execution") if isinstance(manifest.get("execution"), dict) else None
    if not execution and isinstance(manifest.get("runner"), dict):
        execution = manifest.get("runner")

    native = isinstance(execution, dict) and str(execution.get("type") or "").strip() == "python_callable"
    if native:
        status = COMPAT_NATIVE
        requires_confirmation = bool(manifest.get("requires_confirmation", False))
    else:
        status = _status_for_non_native(capabilities=capabilities)
        requires_confirmation = source == "imported" and status != COMPAT_DOCUMENT_ONLY

    missing = _dependency_diagnostics(capabilities)
    diagnostics.extend(missing)

    scripts = _detect_scripts(folder=folder, content=content, diagnostics=diagnostics)
    shell_commands = _detect_shell_commands(content)
    if shell_commands and CAPABILITY_SHELL in capabilities:
        blocked = [command for command in shell_commands if not _is_allowed_shell_prefix(command)]
        if blocked:
            diagnostics.append("发现不在兼容白名单内的命令：" + "；".join(blocked[:3]))
            if status == COMPAT_COMPATIBLE:
                status = COMPAT_UNSUPPORTED

    if CAPABILITY_WEB_SEARCH in capabilities:
        diagnostics.append("web_search 能力已识别，但当前还没有配置 Brave/Tavily 等搜索供应商。")

    if status == COMPAT_COMPATIBLE and not _has_runnable_capability(capabilities):
        status = COMPAT_DOCUMENT_ONLY

    risk_level = _infer_risk_level(capabilities=capabilities, source=source, diagnostics=diagnostics)
    compatibility = {
        "status": status,
        "source_format": "sb-agent" if native else "github-skill" if source == "imported" else "skill-md",
        "reason": _compatibility_reason(status),
    }

    return {
        "compatibility": compatibility,
        "capabilities": tuple(sorted(capabilities)),
        "risk_level": risk_level,
        "install_source": install_source,
        "diagnostics": diagnostics,
        "compatible_execution": _build_compatible_execution(
            folder=folder,
            name=name,
            description=description,
            capabilities=capabilities,
            status=status,
            shell_commands=shell_commands,
            scripts=scripts,
        ),
        "compatible_input_schema": COMPATIBLE_INPUT_SCHEMA if status == COMPAT_COMPATIBLE else None,
        "compatible_tool_name": _safe_tool_name(name) if status == COMPAT_COMPATIBLE else None,
        "requires_confirmation": requires_confirmation,
    }


def _status_for_non_native(*, capabilities: set[str]) -> str:
    if _has_runnable_capability(capabilities):
        return COMPAT_COMPATIBLE
    return COMPAT_DOCUMENT_ONLY


def _has_runnable_capability(capabilities: set[str]) -> bool:
    return bool(capabilities.intersection({CAPABILITY_SHELL, CAPABILITY_HTTP, CAPABILITY_BROWSER, CAPABILITY_WEB_FETCH}))


def _capabilities_from_declared_tools(frontmatter: dict[str, Any], manifest: dict[str, Any]) -> set[str]:
    values: list[str] = []
    for key in ("allowed-tools", "allowed_tools", "tools", "tool_names", "capabilities"):
        values.extend(_as_text_list(frontmatter.get(key)))
        values.extend(_as_text_list(manifest.get(key)))

    text = " ".join(values).lower()
    capabilities: set[str] = set()
    if any(token in text for token in ("bash", "shell", "terminal", "curl", "node", "npx")):
        capabilities.add(CAPABILITY_SHELL)
    if any(token in text for token in ("webfetch", "web_fetch", "fetch", "curl", "http")):
        capabilities.add(CAPABILITY_HTTP)
        capabilities.add(CAPABILITY_WEB_FETCH)
    if any(token in text for token in ("agent-browser", "agent_browser", "browser", "playwright")):
        capabilities.add(CAPABILITY_BROWSER)
    if any(token in text for token in ("websearch", "web_search", "search")):
        capabilities.add(CAPABILITY_WEB_SEARCH)
    return capabilities


def _capabilities_from_content(content: str) -> set[str]:
    text = content.lower()
    capabilities: set[str] = set()
    if _BASH_TOOL_RE.search(content) or _SHELL_PATTERN_RE.search(content):
        capabilities.add(CAPABILITY_SHELL)
    if "curl " in text or "httpx" in text or "webfetch" in text or "web_fetch" in text:
        capabilities.add(CAPABILITY_HTTP)
        capabilities.add(CAPABILITY_WEB_FETCH)
    if "agent-browser" in text or "agent_browser" in text or "playwright" in text:
        capabilities.add(CAPABILITY_BROWSER)
    if "websearch" in text or "web_search" in text or "tavily" in text or "brave" in text:
        capabilities.add(CAPABILITY_WEB_SEARCH)
    return capabilities


def _detect_shell_commands(content: str) -> tuple[str, ...]:
    commands: list[str] = []
    for match in _BASH_TOOL_RE.finditer(content):
        command = match.group(1).strip().strip('"').strip("'")
        if command and not _is_tool_pattern_declaration(command):
            cleaned = _clean_shell_command(command)
            if cleaned:
                commands.append(cleaned)
    for match in _SHELL_PATTERN_RE.finditer(content):
        command = _clean_shell_command(match.group(1))
        if command and not _is_tool_pattern_declaration(command):
            commands.append(command)
    for fence in _COMMAND_FENCE_RE.finditer(content):
        body = fence.group("body")
        for raw_line in body.splitlines():
            line = _clean_shell_command(raw_line)
            if _is_allowed_shell_prefix(line):
                commands.append(line)
    return tuple(_deduplicate(commands))


def _clean_shell_command(value: str) -> str:
    """把文档里的示例命令整理成可校验的单行命令。"""
    command = _one_line(value).strip()
    if not command:
        return ""
    if "#" in command:
        command = command.split("#", 1)[0].strip()
    if not _is_allowed_shell_prefix(command):
        return ""
    if command.startswith("agent-browser") and not _is_known_agent_browser_command(command):
        return ""
    return command


def _is_tool_pattern_declaration(command: str) -> bool:
    normalized = command.strip()
    if re.fullmatch(r"[a-zA-Z0-9_-]+:\*", normalized):
        return True
    return bool(re.fullmatch(r"npx\s+[a-zA-Z0-9_-]+:\*", normalized))


def _detect_scripts(*, folder: Path, content: str, diagnostics: list[str]) -> tuple[str, ...]:
    scripts: list[str] = []
    for match in _SCRIPT_RE.finditer(content):
        rel_path = match.group(1)
        if rel_path in scripts:
            continue
        scripts.append(rel_path)
        if not (folder / rel_path).exists():
            diagnostics.append(f"声明了脚本但文件不存在：{rel_path}")
    return tuple(scripts)


def _dependency_diagnostics(capabilities: set[str]) -> list[str]:
    diagnostics: list[str] = []
    if CAPABILITY_BROWSER in capabilities and shutil.which("agent-browser") is None:
        diagnostics.append("缺少 agent-browser CLI，可运行 npm install -g agent-browser && agent-browser install。")
    if CAPABILITY_SHELL in capabilities:
        if shutil.which("node") is None:
            diagnostics.append("缺少 node，部分兼容脚本可能无法执行。")
        if shutil.which("npx") is None:
            diagnostics.append("缺少 npx，部分兼容脚本可能无法执行。")
    return diagnostics


def _build_compatible_execution(
    *,
    folder: Path,
    name: str,
    description: str,
    capabilities: set[str],
    status: str,
    shell_commands: tuple[str, ...],
    scripts: tuple[str, ...],
) -> dict[str, Any] | None:
    if status != COMPAT_COMPATIBLE:
        return None
    return {
        "type": "compatible_skill",
        "skill_dir": str(folder),
        "capabilities": sorted(capabilities),
        "allowed_shell_prefixes": list(SAFE_SHELL_PREFIXES),
        "detected_shell_commands": list(shell_commands),
        "detected_scripts": list(scripts),
        "description": description,
        "tool_name": _safe_tool_name(name),
    }


def _infer_risk_level(*, capabilities: set[str], source: str, diagnostics: list[str]) -> str:
    if CAPABILITY_SHELL in capabilities:
        return RISK_HIGH
    if source == "imported" and capabilities.intersection({CAPABILITY_BROWSER, CAPABILITY_HTTP, CAPABILITY_WEB_FETCH}):
        return RISK_MEDIUM
    if diagnostics:
        return RISK_MEDIUM
    return RISK_LOW


def _compatibility_reason(status: str) -> str:
    if status == COMPAT_NATIVE:
        return "包含 sb-agent 原生 skill.json，可直接按 python_callable 执行。"
    if status == COMPAT_COMPATIBLE:
        return "没有原生执行配置，但识别出可包装的常见工具能力。"
    if status == COMPAT_DOCUMENT_ONLY:
        return "只有 SKILL.md 说明，可注入模型上下文，但不会生成工具。"
    if status == COMPAT_UNSUPPORTED:
        return "识别到当前兼容层不能安全执行的命令或能力。"
    return "解析或校验出错。"


def _is_allowed_shell_prefix(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    return any(stripped == prefix or stripped.startswith(prefix + " ") for prefix in SAFE_SHELL_PREFIXES)


def _is_known_agent_browser_command(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts or parts[0] != "agent-browser":
        return False
    index = 1
    while index < len(parts):
        token = parts[index]
        if token in AGENT_BROWSER_COMMANDS:
            return True
        if not token.startswith("-"):
            return False
        index += 1
        if token in {"--session", "-s", "--profile", "-p", "--state", "--timeout"} and index < len(parts):
            index += 1
    return False


def _safe_tool_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_") or "compatible_skill"


def _as_text_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _one_line(value: str) -> str:
    return " ".join(str(value).strip().split())


def _deduplicate(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def ensure_supported_compatibility(status: str, diagnostics: list[str]) -> None:
    if status in {COMPAT_NATIVE, COMPAT_COMPATIBLE, COMPAT_DOCUMENT_ONLY}:
        return
    detail = "；".join(diagnostics) if diagnostics else "没有可用的兼容执行方式。"
    raise DailyAgentError(f"这个 skill 暂不支持导入为可执行能力：{detail}")
