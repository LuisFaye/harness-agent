from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from daily_agent.errors import DailyAgentError
from daily_agent.skill_compat import (
    CAPABILITY_BROWSER,
    CAPABILITY_HTTP,
    CAPABILITY_SHELL,
    CAPABILITY_WEB_FETCH,
    SAFE_SHELL_PREFIXES,
)
from daily_agent.skill_runtime import (
    SKILL_STATUS_ERROR,
    SKILL_STATUS_SUCCESS,
    SkillExecutionContext,
    SkillRunResult,
)

MAX_OUTPUT_CHARS = 12000
MAX_HTTP_BYTES = 200_000
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 300
BLOCKED_COMMAND_PATTERNS = (
    re.compile(r"\brm\s+-[^\n;]*r[f]?\b", re.IGNORECASE),
    re.compile(r"\b(?:mkfs|fdisk|diskutil\s+erase|dd\s+if=|shutdown|reboot)\b", re.IGNORECASE),
    re.compile(r"\b(?:sudo\s+)?(?:chmod|chown)\s+(?:-[^\s]+\s+)?(?:/|~)", re.IGNORECASE),
    re.compile(r"(?:~/.ssh|/\.ssh/|\.env\b|api[_-]?key|secret[_-]?key|access[_-]?token)", re.IGNORECASE),
    re.compile(r"(?:&\s*$|nohup|disown|setsid)", re.IGNORECASE),
)


@dataclass(frozen=True)
class CompatibleSkillRuntime:
    """第三方 SKILL.md 的受控兼容执行器。"""

    def run(self, *, context: SkillExecutionContext) -> SkillRunResult:
        execution = context.skill.execution or {}
        capabilities = set(_as_text_list(execution.get("capabilities") or context.skill.capabilities))
        inputs = dict(context.inputs or {})
        structured_inputs = dict(inputs.get("inputs") or {}) if isinstance(inputs.get("inputs"), dict) else {}
        timeout_seconds = _timeout_seconds(inputs.get("timeout_seconds") or structured_inputs.get("timeout_seconds"))

        try:
            if _has_explicit_command(inputs=inputs, structured_inputs=structured_inputs):
                return self._run_shell(
                    context=context,
                    inputs=inputs,
                    structured_inputs=structured_inputs,
                    timeout_seconds=timeout_seconds,
                )
            if CAPABILITY_BROWSER in capabilities:
                return self._run_browser(
                    context=context,
                    inputs=inputs,
                    structured_inputs=structured_inputs,
                    timeout_seconds=timeout_seconds,
                )
            if capabilities.intersection({CAPABILITY_HTTP, CAPABILITY_WEB_FETCH}):
                return self._run_http(context=context, inputs=inputs, structured_inputs=structured_inputs, timeout_seconds=timeout_seconds)
            if CAPABILITY_SHELL in capabilities:
                return self._run_shell(context=context, inputs=inputs, structured_inputs=structured_inputs, timeout_seconds=timeout_seconds)
            raise DailyAgentError("这个兼容 skill 没有可执行能力，只能作为说明文档注入上下文。")
        except DailyAgentError as exc:
            return SkillRunResult(
                status=SKILL_STATUS_ERROR,
                response_text=str(exc),
                stop_reason="兼容 skill 执行失败",
                inputs=_safe_inputs(inputs),
                clear_session=True,
            )

    def _run_browser(
        self,
        *,
        context: SkillExecutionContext,
        inputs: dict[str, Any],
        structured_inputs: dict[str, Any],
        timeout_seconds: int,
    ) -> SkillRunResult:
        if _can_use_agent_browser_chat():
            return self._run_agent_browser_chat(
                context=context,
                inputs=inputs,
                structured_inputs=structured_inputs,
                timeout_seconds=timeout_seconds,
            )
        raise DailyAgentError(
            "这个第三方浏览器 skill 需要执行具体的 agent-browser 命令。"
            "请在 inputs.command 里传入白名单命令，例如 "
            "`agent-browser open https://example.com`、`agent-browser snapshot -i`。"
            "当前未配置 AI_GATEWAY_API_KEY，因此不能使用 agent-browser chat 自动规划浏览器步骤。"
        )

    def _run_agent_browser_chat(
        self,
        *,
        context: SkillExecutionContext,
        inputs: dict[str, Any],
        structured_inputs: dict[str, Any],
        timeout_seconds: int,
    ) -> SkillRunResult:
        binary = shutil.which("agent-browser")
        if not binary:
            raise DailyAgentError("本机没有找到 agent-browser。请先运行：npm install -g agent-browser && agent-browser install")

        instruction = _browser_chat_instruction(context=context, inputs=inputs, structured_inputs=structured_inputs)
        command_args = [binary, "chat", instruction]
        try:
            completed = subprocess.run(
                command_args,
                cwd=str(Path(context.working_directory).expanduser().resolve()),
                env=_subprocess_environment(context, preserve_user_home=True, include_gateway_key=True),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise DailyAgentError(f"agent-browser chat 执行超时，超过 {timeout_seconds} 秒没有完成。") from exc
        except OSError as exc:
            raise DailyAgentError(f"agent-browser chat 执行失败：{exc}") from exc

        output = _compact_output((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""))
        if completed.returncode != 0:
            raise DailyAgentError(f"agent-browser chat 返回非零状态码 {completed.returncode}：{output or '无输出'}")

        return SkillRunResult(
            status=SKILL_STATUS_SUCCESS,
            response_text=f"兼容 skill 已完成浏览器任务：`agent-browser chat`\n\n{output or '命令没有输出。'}",
            stop_reason="兼容浏览器任务完成",
            inputs=_safe_inputs(inputs),
            output_text=output,
            observation={
                "capability": "browser",
                "command": "agent-browser chat",
                "returncode": completed.returncode,
                "isolation": "browser_profile",
            },
            clear_session=True,
        )

    def _run_http(
        self,
        *,
        context: SkillExecutionContext,
        inputs: dict[str, Any],
        structured_inputs: dict[str, Any],
        timeout_seconds: int,
    ) -> SkillRunResult:
        url = str(inputs.get("url") or structured_inputs.get("url") or "").strip()
        if not url:
            raise DailyAgentError("http/web_fetch 兼容 skill 需要提供 url。")
        if not url.startswith(("http://", "https://")):
            raise DailyAgentError("url 必须以 http:// 或 https:// 开头。")

        method = str(structured_inputs.get("method") or "GET").upper()
        if method not in {"GET", "POST"}:
            raise DailyAgentError("兼容 HTTP 执行器只支持 GET 和 POST。")
        headers = structured_inputs.get("headers") if isinstance(structured_inputs.get("headers"), dict) else {}
        data = structured_inputs.get("data")

        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=True, max_redirects=5) as client:
                response = client.request(method, url, headers=headers, data=data)
                body = response.content[:MAX_HTTP_BYTES]
        except httpx.HTTPError as exc:
            raise DailyAgentError(f"HTTP 请求失败：{exc}") from exc

        text = _decode_response(body, response)
        output = _compact_output(text)
        response_text = (
            f"兼容 skill 已完成 HTTP 请求：{method} {url}\n"
            f"状态码：{response.status_code}\n\n"
            f"{output or '响应体为空。'}"
        )
        return SkillRunResult(
            status=SKILL_STATUS_SUCCESS,
            response_text=response_text,
            stop_reason="兼容 HTTP 请求完成",
            inputs=_safe_inputs(inputs),
            output_text=output,
            observation={
                "capability": "http",
                "url": url,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type"),
                "truncated": len(response.content) > MAX_HTTP_BYTES or len(text) > MAX_OUTPUT_CHARS,
            },
            clear_session=True,
        )

    def _run_shell(
        self,
        *,
        context: SkillExecutionContext,
        inputs: dict[str, Any],
        structured_inputs: dict[str, Any],
        timeout_seconds: int,
    ) -> SkillRunResult:
        execution = context.skill.execution or {}
        command = _select_shell_command(
            execution=execution,
            inputs=inputs,
            structured_inputs=structured_inputs,
        )
        _validate_shell_command(command)
        command_args = shlex.split(command)
        if not command_args:
            raise DailyAgentError("shell 命令为空。")
        binary = shutil.which(command_args[0])
        if not binary:
            raise DailyAgentError(f"找不到命令：{command_args[0]}")
        command_args[0] = binary
        cwd = _resolve_cwd(
            requested=structured_inputs.get("cwd"),
            skill_dir=Path(str(execution.get("skill_dir") or context.skill.path)).expanduser(),
            project_dir=Path(context.working_directory).expanduser(),
        )

        try:
            preserve_user_home = Path(command_args[0]).name == "agent-browser"
            isolation_mode = "browser_profile" if preserve_user_home else "private_process"
            if context.isolation is not None:
                command_args, isolation_mode = context.isolation.wrap_command(
                    command_args,
                    project_directory=context.working_directory,
                    preserve_user_home=preserve_user_home,
                )
            completed = subprocess.run(
                command_args,
                cwd=str(cwd),
                env=_subprocess_environment(context, preserve_user_home=preserve_user_home),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise DailyAgentError(f"shell 命令执行超时，超过 {timeout_seconds} 秒没有完成。") from exc
        except OSError as exc:
            raise DailyAgentError(f"shell 命令执行失败：{exc}") from exc

        output = _compact_output((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""))
        if completed.returncode != 0:
            raise DailyAgentError(f"shell 命令返回非零状态码 {completed.returncode}：{output or '无输出'}")

        return SkillRunResult(
            status=SKILL_STATUS_SUCCESS,
            response_text=f"兼容 skill 已完成命令：`{_redact_command(command)}`\n\n{output or '命令没有输出。'}",
            stop_reason="兼容 shell 命令完成",
            inputs=_safe_inputs(inputs),
            output_text=output,
            observation={
                "capability": "shell",
                "command": _redact_command(command),
                "cwd": str(cwd),
                "returncode": completed.returncode,
                "isolation": isolation_mode,
            },
            clear_session=True,
        )


def _select_shell_command(
    *,
    execution: dict[str, Any],
    inputs: dict[str, Any],
    structured_inputs: dict[str, Any],
) -> str:
    explicit = structured_inputs.get("command") or inputs.get("command")
    if explicit:
        return str(explicit).strip()

    scripts = _as_text_list(execution.get("detected_scripts"))
    if scripts:
        script = scripts[0]
        if script.endswith((".mjs", ".js", ".cjs")):
            return f"node {script}"

    commands = _as_text_list(execution.get("detected_shell_commands"))
    if commands:
        return commands[0]

    raise DailyAgentError("这个 shell 兼容 skill 没有可执行命令。请在 inputs.command 里提供 node、npx、agent-browser 或 curl 命令。")


def _has_explicit_command(*, inputs: dict[str, Any], structured_inputs: dict[str, Any]) -> bool:
    return bool(str(structured_inputs.get("command") or inputs.get("command") or "").strip())


def _can_use_agent_browser_chat() -> bool:
    return bool(os.getenv("AI_GATEWAY_API_KEY", "").strip())


def _browser_chat_instruction(
    *,
    context: SkillExecutionContext,
    inputs: dict[str, Any],
    structured_inputs: dict[str, Any],
) -> str:
    instruction = str(
        structured_inputs.get("instruction")
        or structured_inputs.get("task")
        or inputs.get("instruction")
        or inputs.get("task")
        or ""
    ).strip()
    url = str(structured_inputs.get("url") or inputs.get("url") or "").strip()
    if instruction and url:
        return f"打开 {url}，然后完成这个任务：{instruction}"
    if instruction:
        return instruction
    if url:
        return f"打开 {url}，并总结页面主要内容。"
    query = str(context.query or "").strip()
    if query:
        return query
    raise DailyAgentError("agent-browser chat 需要 task、instruction、url 或原始问题。")


def _validate_shell_command(command: str) -> None:
    stripped = command.strip()
    if not stripped:
        raise DailyAgentError("shell 命令为空。")
    if any(pattern.search(stripped) for pattern in BLOCKED_COMMAND_PATTERNS):
        raise DailyAgentError("这个命令命中了安全拦截规则，已拒绝执行。")
    if not any(stripped == prefix or stripped.startswith(prefix + " ") for prefix in SAFE_SHELL_PREFIXES):
        raise DailyAgentError("兼容 shell 执行器只允许 node、npx、agent-browser、curl 这几类命令前缀。")
    if any(token in stripped for token in (";", "&&", "||", "|", "`", "$(", ">", "<")):
        raise DailyAgentError("兼容 shell 执行器不接受管道、重定向或多命令拼接。")


def _resolve_cwd(*, requested: Any, skill_dir: Path, project_dir: Path) -> Path:
    roots = [skill_dir.resolve(), project_dir.resolve()]
    if requested:
        candidate = Path(str(requested)).expanduser()
        if not candidate.is_absolute():
            candidate = skill_dir / candidate
    else:
        candidate = skill_dir
    resolved = candidate.resolve()
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise DailyAgentError("shell 工作目录必须在 skill 目录或当前项目目录内。")
    return resolved


def _timeout_seconds(value: Any) -> int:
    try:
        timeout = int(value or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(timeout, MAX_TIMEOUT_SECONDS))


def _decode_response(body: bytes, response: httpx.Response) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _compact_output(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= MAX_OUTPUT_CHARS:
        return cleaned
    return cleaned[:MAX_OUTPUT_CHARS] + "\n...（输出过长，已截断）"


def _safe_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(inputs, ensure_ascii=False, default=str))


def _as_text_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _redact_command(command: str) -> str:
    parts = shlex.split(command)
    redacted: list[str] = []
    skip_next = False
    for index, part in enumerate(parts):
        if skip_next:
            redacted.append("***")
            skip_next = False
            continue
        lowered = part.lower()
        if lowered in {"-h", "--header", "-u", "--user", "--proxy-user"}:
            redacted.append(part)
            skip_next = True
            continue
        if any(token in lowered for token in ("token", "secret", "apikey", "api_key", "authorization")):
            redacted.append("***")
            continue
        redacted.append(part if index > 0 else Path(part).name)
    return " ".join(shlex.quote(part) for part in redacted)


def _subprocess_environment(
    context: SkillExecutionContext,
    *,
    preserve_user_home: bool = False,
    include_gateway_key: bool = False,
) -> dict[str, str]:
    if context.isolation is not None:
        return context.isolation.subprocess_environment(
            preserve_user_home=preserve_user_home,
            include_gateway_key=include_gateway_key,
        )

    # 兼容直接构造 SkillExecutionContext 的旧调用方，但仍不再默认继承整个主进程环境。
    environment = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
        if os.environ.get(key)
    }
    environment["HOME"] = str(Path.home())
    if include_gateway_key and os.environ.get("AI_GATEWAY_API_KEY"):
        environment["AI_GATEWAY_API_KEY"] = os.environ["AI_GATEWAY_API_KEY"]
    return environment
