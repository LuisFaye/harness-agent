"""agent-browser CLI 适配 skill handler。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError
from daily_agent.skill_runtime import (
    SKILL_STATUS_ERROR,
    SKILL_STATUS_SUCCESS,
    SkillExecutionContext,
    SkillRunResult,
)

MAX_OUTPUT_CHARS = 12000
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 300
SUPPORTED_ACTIONS = {
    "chat",
    "open",
    "snapshot",
    "click",
    "fill",
    "press",
    "wait",
    "screenshot",
    "get_text",
    "get_title",
    "get_url",
    "close",
}


def run(*, context: SkillExecutionContext) -> SkillRunResult:
    """执行一次 agent-browser CLI 调用。"""
    inputs = dict(context.inputs)
    try:
        command = _build_command(inputs=inputs, query=context.query, working_directory=context.working_directory)
        completed = _run_command(command, timeout_seconds=_timeout_seconds(inputs.get("timeout_seconds")))
        output = _compact_output((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""))
        response = _render_response(command=command, output=output)
        return SkillRunResult(
            status=SKILL_STATUS_SUCCESS,
            response_text=response,
            stop_reason="浏览器操作完成",
            inputs=_safe_inputs(inputs),
            output_text=output,
            observation={
                "command": _redact_command(command),
                "returncode": completed.returncode,
                "output_chars": len(output),
            },
            clear_session=True,
        )
    except DailyAgentError as exc:
        return SkillRunResult(
            status=SKILL_STATUS_ERROR,
            response_text=str(exc),
            stop_reason="浏览器操作失败",
            inputs=_safe_inputs(inputs),
            clear_session=True,
        )


def _build_command(*, inputs: dict[str, Any], query: str, working_directory: str) -> list[str]:
    binary = shutil.which("agent-browser")
    if not binary:
        raise DailyAgentError("本机没有找到 agent-browser。请先运行：npm install -g agent-browser && agent-browser install")

    action = str(inputs.get("action") or "").strip().lower().replace("-", "_")
    if not action:
        raise DailyAgentError("浏览器操作必须明确指定 action，例如 open、snapshot、fill、click 或 screenshot。")
    if action not in SUPPORTED_ACTIONS:
        raise DailyAgentError(f"不支持的浏览器动作：{action or '空'}")

    if action == "chat":
        if not os.getenv("AI_GATEWAY_API_KEY", "").strip():
            raise DailyAgentError(
                "当前没有配置 AI_GATEWAY_API_KEY，不能使用 agent-browser chat。"
                "请改用 open、snapshot、fill、click、wait、screenshot 等单步动作完成任务。"
            )
        instruction = _chat_instruction(inputs=inputs, query=query)
        return [binary, "chat", instruction]
    if action == "open":
        url = _required_text(inputs, "url", "open 动作需要 url。")
        return [binary, "open", url]
    if action == "snapshot":
        return [binary, "snapshot"]
    if action == "click":
        return [binary, "click", _required_text(inputs, "selector", "click 动作需要 selector。")]
    if action == "fill":
        selector = _required_text(inputs, "selector", "fill 动作需要 selector。")
        text = _required_text(inputs, "text", "fill 动作需要 text。")
        return [binary, "fill", selector, text]
    if action == "press":
        return [binary, "press", _required_text(inputs, "text", "press 动作需要 text，例如 Enter。")]
    if action == "wait":
        return _wait_command(binary, inputs)
    if action == "screenshot":
        return _screenshot_command(binary, inputs, working_directory)
    if action == "get_text":
        return [binary, "get", "text", _required_text(inputs, "selector", "get_text 动作需要 selector。")]
    if action == "get_title":
        return [binary, "get", "title"]
    if action == "get_url":
        return [binary, "get", "url"]
    if action == "close":
        return [binary, "close"]
    raise DailyAgentError(f"不支持的浏览器动作：{action}")


def _run_command(command: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise DailyAgentError(f"agent-browser 执行超时，超过 {timeout_seconds} 秒没有完成。") from exc
    except OSError as exc:
        raise DailyAgentError(f"agent-browser 执行失败：{exc}") from exc

    if completed.returncode != 0:
        output = _compact_output((completed.stderr or "") + ("\n" + completed.stdout if completed.stdout else ""))
        raise DailyAgentError(f"agent-browser 返回非零状态码 {completed.returncode}：{output or '无输出'}")
    return completed


def _chat_instruction(*, inputs: dict[str, Any], query: str) -> str:
    instruction = str(inputs.get("instruction") or "").strip()
    url = str(inputs.get("url") or "").strip()
    if instruction and url:
        return f"打开 {url}，然后完成这个任务：{instruction}"
    if instruction:
        return instruction
    if url:
        return f"打开 {url}，并总结页面主要内容。"
    query_text = str(query or "").strip()
    if query_text:
        return query_text
    raise DailyAgentError("chat 动作需要 instruction、url 或原始问题。")


def _wait_command(binary: str, inputs: dict[str, Any]) -> list[str]:
    selector = str(inputs.get("selector") or "").strip()
    text = str(inputs.get("text") or "").strip()
    if selector:
        return [binary, "wait", selector]
    if text:
        if text.isdigit():
            return [binary, "wait", text]
        return [binary, "wait", "--text", text]
    raise DailyAgentError("wait 动作需要 selector 或 text。")


def _screenshot_command(binary: str, inputs: dict[str, Any], working_directory: str) -> list[str]:
    command = [binary, "screenshot"]
    path = str(inputs.get("path") or "").strip()
    if path:
        screenshot_path = Path(path).expanduser()
        if not screenshot_path.is_absolute():
            screenshot_path = Path(working_directory) / screenshot_path
        command.append(str(screenshot_path.resolve()))
    if bool(inputs.get("full_page")):
        command.append("--full")
    return command


def _required_text(inputs: dict[str, Any], key: str, message: str) -> str:
    value = str(inputs.get(key) or "").strip()
    if not value:
        raise DailyAgentError(message)
    return value


def _timeout_seconds(value: Any) -> int:
    try:
        timeout = int(value or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(timeout, MAX_TIMEOUT_SECONDS))


def _compact_output(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= MAX_OUTPUT_CHARS:
        return cleaned
    return cleaned[:MAX_OUTPUT_CHARS] + "\n...（输出过长，已截断）"


def _render_response(*, command: list[str], output: str) -> str:
    action = " ".join(_redact_command(command)[1:3]).strip()
    if output:
        return f"浏览器操作已完成：`{action}`\n\n{output}"
    return f"浏览器操作已完成：`{action}`，没有额外输出。"


def _safe_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(inputs, ensure_ascii=False, default=str))


def _redact_command(command: list[str]) -> list[str]:
    if not command:
        return []
    redacted = list(command)
    redacted[0] = "agent-browser"
    return redacted
