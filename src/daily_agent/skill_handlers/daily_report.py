"""日报 skill handler。

这个 handler 负责把“日报 skill”真正接到 ask 会话里：
1. 识别仓库路径、日期、作者等参数
2. 先扫描提交数据
3. 再追问用户是否有代码之外的附加工作
4. 用户补完后生成最终日报

这样主程序不需要知道“日报 skill 该怎么做”，以后新增 skill 只需要新增自己的 handler。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError
from daily_agent.executor import extract_dates
from daily_agent.services.report_service import DailyReportService
from daily_agent.skill_runtime import (
    SKILL_STATUS_ERROR,
    SKILL_STATUS_NEEDS_INPUT,
    SKILL_STATUS_SUCCESS,
    SkillExecutionContext,
    SkillRunResult,
)

NONE_EXTRA_NOTE_TOKENS = (
    "没有",
    "没有了",
    "没了",
    "无",
    "无补充",
    "不用补充",
    "无需补充",
    "就这些",
)
PATH_RE = re.compile(r"(?P<path>(?:~|\.{1,2}|/)[^\s,，;；]+)")


def run(*, context: SkillExecutionContext) -> SkillRunResult:
    """执行日报 skill。"""
    try:
        if context.state.get("mode") == "tool":
            return _oneshot_report(context)
        if context.state.get("phase") == "awaiting_extra_notes":
            return _finish_report(context)
        return _start_report(context)
    except DailyAgentError as exc:
        return SkillRunResult(
            status=SKILL_STATUS_ERROR,
            response_text=str(exc),
            stop_reason="日报技能执行失败",
            inputs=dict(context.inputs),
            state=dict(context.state),
        )


def _oneshot_report(context: SkillExecutionContext) -> SkillRunResult:
    """作为 ReAct 工具被调用时：一次扫描 + 渲染，附加工作由模型在调用前问清楚后传进来。"""
    inputs = dict(context.inputs)
    repos = inputs.get("repos") or _extract_repos(context.query, context.working_directory)
    if not repos:
        return SkillRunResult(
            status=SKILL_STATUS_NEEDS_INPUT,
            response_text="要生成日报，请告诉我扫描哪个 git 仓库（可以是路径，或说“当前项目”）。",
            stop_reason="缺少日报仓库路径",
            inputs=inputs,
            missing_info=["repos"],
        )
    if isinstance(repos, str):
        repos = [repos]

    target_date = inputs.get("target_date") or inputs.get("date") or _extract_target_date(context.query)
    author = inputs.get("author") or _extract_author(context.query)
    extra_notes = _normalize_extra_notes(inputs.get("extra_notes"))

    payload = DailyReportService().scan(
        target_date=_parse_date(target_date),
        repos=repos,
        author=author,
    )
    report_text = DailyReportService().render_report(payload=payload, extra_notes=extra_notes)
    return SkillRunResult(
        status=SKILL_STATUS_SUCCESS,
        response_text=report_text,
        stop_reason="日报已生成",
        inputs={"repos": repos, "target_date": target_date, "author": author, "extra_notes": extra_notes},
        output_text=report_text,
        observation={
            "total_commits": int(payload.get("total_commits", 0)),
            "repositories": _repo_names(payload),
            "extra_notes": extra_notes,
        },
        clear_session=True,
    )


def _normalize_extra_notes(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return _extract_extra_notes(value)
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()][:5]
    return []


def _start_report(context: SkillExecutionContext) -> SkillRunResult:
    inputs = dict(context.inputs)
    repos = inputs.get("repos") or _extract_repos(context.query, context.working_directory)
    target_date = inputs.get("date") or _extract_target_date(context.query)
    author = inputs.get("author") or _extract_author(context.query)

    if not repos:
        return SkillRunResult(
            status=SKILL_STATUS_NEEDS_INPUT,
            response_text="要生成日报，我还需要知道扫描哪个 git 仓库。你可以直接发仓库路径，也可以说“当前项目”。",
            stop_reason="缺少日报仓库路径",
            inputs={"date": target_date, "author": author},
            state={"phase": "awaiting_repos"},
            missing_info=["repos"],
        )

    service = DailyReportService()
    payload = service.scan(
        target_date=_parse_date(target_date),
        repos=repos,
        author=author,
    )
    total_commits = int(payload.get("total_commits", 0))
    repo_names = _repo_names(payload)
    repo_text = "、".join(repo_names[:3]) if repo_names else "当前仓库"
    prompt = (
        f"我先帮你扫完了，{target_date} 在 {repo_text} 共发现 {total_commits} 条提交。"
        "如果今天还有代码之外的工作，比如会议、沟通、排查、写文档，也可以直接补充给我；"
        "如果没有，回复“没有了”就行。"
    )
    merged_inputs = {
        "repos": repos,
        "date": target_date,
        "author": author,
    }
    return SkillRunResult(
        status=SKILL_STATUS_NEEDS_INPUT,
        response_text=prompt,
        stop_reason="等待补充日报附加工作",
        inputs=merged_inputs,
        state={"phase": "awaiting_extra_notes", "scan_payload": payload},
        missing_info=["extra_notes"],
        observation={"total_commits": total_commits, "repositories": repo_names},
    )


def _finish_report(context: SkillExecutionContext) -> SkillRunResult:
    payload = context.state.get("scan_payload")
    if not isinstance(payload, dict):
        raise DailyAgentError("日报会话状态已损坏，缺少扫描结果，请重新发起一次日报生成。")

    extra_notes = _extract_extra_notes(context.query)
    merged_inputs = dict(context.inputs)
    merged_inputs["extra_notes"] = extra_notes

    report_text = DailyReportService().render_report(payload=payload, extra_notes=extra_notes)
    return SkillRunResult(
        status=SKILL_STATUS_SUCCESS,
        response_text=report_text,
        stop_reason="日报已生成",
        inputs=merged_inputs,
        output_text=report_text,
        observation={
            "total_commits": int(payload.get("total_commits", 0)),
            "repositories": _repo_names(payload),
            "extra_notes": extra_notes,
        },
        clear_session=True,
    )


def _extract_target_date(query: str) -> str:
    dates = extract_dates(query, today=date.today())
    return dates[0] if dates else date.today().isoformat()


def _extract_repos(query: str, working_directory: str) -> list[str]:
    """尽量从用户表达里识别仓库路径，识别不到时默认当前项目。"""
    candidates: list[str] = []
    for match in PATH_RE.finditer(query):
        value = match.group("path").strip().strip("，,。")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (Path(working_directory) / path).resolve()
        else:
            path = path.resolve()
        if path.exists():
            candidates.append(str(path))

    if candidates:
        return _deduplicate(candidates)

    if _contains_any(query, ["当前项目", "当前仓库", "当前目录", "这个项目", "本项目"]):
        if _is_git_repo(working_directory):
            return [str(Path(working_directory).resolve())]

    if _is_git_repo(working_directory):
        return [str(Path(working_directory).resolve())]
    return []


def _extract_author(query: str) -> str | None:
    match = re.search(r"(?:作者|author)\s*[:：]?\s*(?P<author>[^\s,，;；]+)", query, flags=re.IGNORECASE)
    if not match:
        return None
    value = match.group("author").strip()
    return value or None


def _extract_extra_notes(query: str) -> list[str]:
    compact = " ".join(query.split()).strip("；;，,。 ")
    if not compact:
        return []
    if compact in NONE_EXTRA_NOTE_TOKENS:
        return []
    if compact.startswith("补充"):
        compact = compact[2:].strip("：:，,。 ")
    parts = [part.strip("；;，,。 ") for part in re.split(r"[；;\n]+", compact) if part.strip("；;，,。 ")]
    if not parts:
        return []
    return parts[:5]


def _repo_names(payload: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in payload.get("repositories", []):
        name = str(item.get("name") or Path(str(item.get("path") or "")).name or "").strip()
        if name:
            names.append(name)
    return names


def _parse_date(value: str) -> date:
    year, month, day = value.split("-")
    return date(int(year), int(month), int(day))


def _is_git_repo(path: str) -> bool:
    current = Path(path).resolve()
    return (current / ".git").exists()


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(item in text for item in needles)


def _deduplicate(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
