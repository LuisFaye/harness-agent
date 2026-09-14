"""原生受控 Git Tool：状态、差异、历史、提交展示、分支、暂存与确认后提交。

安全边界
--------
- 只操作 Workspace Root 内、真实 git 根也位于 Workspace Root 内的仓库；外置
  gitdir / linked worktree（gitdir 在工作区外）首版明确拒绝。
- repository / path / revision / message 全部经 git_security 校验：拒绝 ..、
  符号链接逃逸、NUL/换行、以 - 开头的路径、Git pathspec magic、通配符、
  敏感路径（.env、凭据、私钥、.ssh/.aws/.codex/.sb-agent 等）和 reflog 选择器。
- 所有 git 子进程都在 sandbox-exec 隔离下运行（隔离后端不可用时工具不挂载）：
  只读 Tool 不能修改仓库；写 Tool 只有经确认后的暂存/提交写入被放行，且只写
  目标仓库 gitdir / .git 入口与 Git 私有 data，不写工作树；网络被沙箱直接拒绝；
  hooks / 外部 diff / textconv / 编辑器 / 签名全部用受控参数与受控环境关闭，
  仓库恶意配置不能获得工作区外访问。
- 公开结果只包含 Workspace Root 相对路径；diff/log 等输出里的路径以返回的
  repo_root（本身是工作区相对路径）为基准，不泄露主机绝对路径。
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import partial
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError
from daily_agent.git_security import (
    CONFLICT_PORCELAIN_CODES,
    GIT_SOURCE_ID,
    MAX_BRANCHES,
    MAX_MESSAGE_CHARS,
    MAX_OUTPUT_CHARS,
    MAX_PATHS,
    MAX_STATUS_FILES,
    RepoInfo,
    get_git_runtime,
    git_runtime_status,
    validate_git_path,
    validate_message,
    validate_revision,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 契约常量

# git log 结构化格式：hash、短 hash、作者名、ISO 时间、主题，用 \x1f 分隔字段、
# \x1e 分隔提交；不输出作者邮箱，避免公开结果泄露邮箱。
LOG_FORMAT = "%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1e"

# git show 元数据格式（不含 patch，patch 单独调用并做字符上限截断）。
SHOW_META_FORMAT = "%H%x1f%h%x1f%an%x1f%aI%x1f%s"

# git_log max_count：1 到 100，默认 20。
DEFAULT_MAX_COUNT = 20
MIN_MAX_COUNT = 1
MAX_MAX_COUNT = 100

# git diff --shortstat 输出解析。
_SHORTSTAT_RE = re.compile(
    r"(?P<files>\d+) files? changed"
    r"(?:, (?P<insertions>\d+) insertions?\(\+\))?"
    r"(?:, (?P<deletions>\d+) deletions?\(-\))?"
)

# git diff --name-status 行解析：状态 + 制表符 + 路径（重命名可能带两列）。
_NAME_STATUS_RE = re.compile(r"^(?P<status>[MADRCU])\t(?P<path1>.+?)(?:\t(?P<path2>.+))?$")


# ---------------------------------------------------------------- Tool 定义

# 七个原生 Git Tool 的稳定定义，ToolRegistry 与 Agent 构建共用。
GIT_TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "git_status",
        "description": (
            "查看工作区内某个 Git 仓库的工作区状态：当前分支或 detached HEAD、"
            "与上游的 ahead/behind、已暂存/未暂存/未跟踪/冲突文件列表。只读，无需确认。"
            "返回的路径都是相对工作区根目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "Git 仓库目录：工作区相对路径或解析后仍位于工作区内的绝对目录，默认 .",
                },
            },
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "git_diff",
        "description": (
            "查看工作区内 Git 仓库的差异：working 模式比较工作区与暂存区，"
            "staged 模式比较暂存区与 HEAD；可按路径限定范围。只读，无需确认；"
            "已关闭外部 diff、textconv 与颜色。patch 超过字符上限会被截断并标记。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "Git 仓库目录，默认 .",
                },
                "mode": {
                    "type": "string",
                    "enum": ["working", "staged"],
                    "description": "working 表示工作区相对暂存区的差异（默认），staged 表示暂存区相对 HEAD 的差异",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_PATHS,
                    "description": "限定差异的文件或目录（工作区相对路径），默认全部",
                },
            },
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "git_log",
        "description": (
            "查看工作区内 Git 仓库的提交历史，返回结构化提交列表：完整 hash、短 hash、"
            "作者名、提交时间和主题；不返回作者邮箱。只读，无需确认。可按 revision 起点"
            "与单一路径过滤。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "Git 仓库目录，默认 .",
                },
                "max_count": {
                    "type": "integer",
                    "minimum": MIN_MAX_COUNT,
                    "maximum": MAX_MAX_COUNT,
                    "description": "最多返回的提交数，1 到 100，默认 20",
                },
                "revision": {
                    "type": "string",
                    "description": "起点 revision（commit hash、分支名、标签名、HEAD 或带 ^/~/数字后缀），默认 HEAD",
                },
                "path": {
                    "type": "string",
                    "description": "只列出影响该文件/目录的提交（工作区相对路径），默认全部",
                },
            },
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "git_show",
        "description": (
            "查看工作区内 Git 仓库的单个提交：返回提交元数据（hash、作者、时间、主题）"
            "和受字符上限约束的差异 patch。只读，无需确认；已关闭外部 diff、textconv"
            "与颜色。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "Git 仓库目录，默认 .",
                },
                "revision": {
                    "type": "string",
                    "description": "要展示的 revision（必填）：commit hash、分支名、标签名或 HEAD",
                },
                "path": {
                    "type": "string",
                    "description": "只展示该文件/目录相关的变更（工作区相对路径），默认全部",
                },
            },
            "required": ["revision"],
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "git_branch_list",
        "description": (
            "列出工作区内 Git 仓库的分支：当前分支、本地分支；include_remote=true 时"
            "同时列出已经存在的远程跟踪分支（只读取本地 remote refs，绝不联网）。"
            "只读，无需确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "Git 仓库目录，默认 .",
                },
                "include_remote": {
                    "type": "boolean",
                    "description": "是否同时返回远程跟踪分支，默认 false",
                },
            },
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "git_add",
        "description": (
            "把明确的文件或目录暂存到工作区内 Git 仓库的索引中。paths 必须提供 1 到 100 "
            "个工作区相对路径，不接受 -A、.、通配符或 pathspec magic；执行前需要用户确认，"
            "确认卡片会展示准备暂存的具体路径。返回实际暂存的文件与状态。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "Git 仓库目录，默认 .",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_PATHS,
                    "description": "要暂存的明确工作区相对路径（1 到 100 个），每个必须是仓库内存在的文件或目录",
                },
            },
            "required": ["paths"],
            "additionalProperties": False,
        },
        "requires_confirmation": True,
    },
    {
        "name": "git_commit",
        "description": (
            "把已暂存的变更提交到工作区内 Git 仓库，message 是 1 到 500 字符的单行提交信息。"
            "提交前会先检查存在已暂存变更，没有则明确拒绝；hooks、签名与编辑器均被受控"
            "参数关闭。执行前需要用户确认，确认卡片会展示提交信息。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repository": {
                    "type": "string",
                    "description": "Git 仓库目录，默认 .",
                },
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_MESSAGE_CHARS,
                    "description": "单行普通提交信息（1 到 500 字符），不允许换行或控制字符",
                },
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        "requires_confirmation": True,
    },
)


# ---------------------------------------------------------------- 通用工具函数


def _clamp_max_count(value: Any) -> int:
    """把 max_count 钳制到 1..100，非法值回落默认 20。"""
    try:
        return max(MIN_MAX_COUNT, min(MAX_MAX_COUNT, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_COUNT


def _repo_relative_path(resolved: Path, repo: RepoInfo) -> str:
    """把工作区内解析后的路径转成相对 git 根的正斜杠路径（不越出仓库）。"""
    try:
        rel = resolved.relative_to(repo.git_root)
    except ValueError as exc:
        raise DailyAgentError("路径不在目标 Git 仓库内，无法操作。") from exc
    return rel.as_posix()


def _workspace_join(repo_root_rel: str, repo_relative: str) -> str:
    """把相对 git 根的路径拼成相对工作区根的 posix 路径。"""
    if repo_root_rel == ".":
        return repo_relative
    return (Path(repo_root_rel) / repo_relative).as_posix()


def _run_or_raise(
    runtime,
    argv: list[str],
    repo: RepoInfo,
    *,
    writable: bool,
    label: str,
) -> dict[str, Any]:
    """运行一次受控 git；非零退出码统一转成中文错误。"""
    result = runtime.run(argv, repo, writable=writable)
    if result["returncode"] != 0:
        detail = str(result["output"]).strip() or "未知错误"
        raise DailyAgentError(f"{label}失败（{detail[:300]}）。")
    if result["timed_out"]:
        raise DailyAgentError(f"{label}超时，已终止。")
    return result


def _parse_shortstat(text: str) -> dict[str, int]:
    """解析 git diff --shortstat 输出；没有变更时返回全 0。"""
    match = _SHORTSTAT_RE.search(str(text or ""))
    if not match:
        return {"files_changed": 0, "insertions": 0, "deletions": 0}
    return {
        "files_changed": int(match.group("files") or 0),
        "insertions": int(match.group("insertions") or 0),
        "deletions": int(match.group("deletions") or 0),
    }


def _parse_log_records(text: str) -> list[dict[str, str]]:
    """解析 git log 的结构化输出为提交字典列表。"""
    commits: list[dict[str, str]] = []
    for record in str(text or "").split("\x1e"):
        # git log --format 每条提交后还会追加一个换行，先去掉记录首尾空白再切字段。
        fields = record.strip().split("\x1f")
        if len(fields) != 5:
            continue
        commits.append(
            {
                "hash": fields[0],
                "short_hash": fields[1],
                "author": fields[2],
                "time": fields[3],
                "subject": fields[4],
            }
        )
    return commits


def _parse_show_meta(text: str) -> dict[str, str]:
    """解析 git show -s 的元数据输出。"""
    fields = str(text or "").strip().split("\x1f")
    if len(fields) != 5:
        return {"hash": "", "short_hash": "", "author": "", "time": "", "subject": ""}
    return {
        "hash": fields[0],
        "short_hash": fields[1],
        "author": fields[2],
        "time": fields[3],
        "subject": fields[4],
    }


def _staged_files(
    runtime,
    repo: RepoInfo,
    *,
    include_unmerged: bool = True,
) -> list[dict[str, str]]:
    """返回当前已暂存文件列表（git diff --cached --name-status）。

    include_unmerged=False 时过滤掉未解决冲突（状态 U）的条目——这些文件不是
    被 git_add 成功暂存的普通变更，git_status 会在 conflicted 列表里单独呈现。
    """
    result = _run_or_raise(
        runtime,
        ["git", "-C", str(repo.git_root), "diff", "--cached", "--name-status", "--no-color"],
        repo,
        writable=False,
        label="读取暂存区",
    )
    items: list[dict[str, str]] = []
    for line in str(result["output"]).splitlines():
        match = _NAME_STATUS_RE.match(line)
        if not match:
            continue
        status = match.group("status")
        if status == "U" and not include_unmerged:
            continue
        path1 = match.group("path1")
        path2 = match.group("path2")
        if status == "R" and path2:
            items.append(
                {
                    "status": "R",
                    "path": _workspace_join(repo.repo_root_rel, path2),
                    "from": _workspace_join(repo.repo_root_rel, path1),
                }
            )
        else:
            items.append({"status": status, "path": _workspace_join(repo.repo_root_rel, path1)})
    return items


def _parse_status_output(
    text: str,
    repo_root_rel: str,
) -> tuple[list[str], list[str], list[str], list[str], str, int, int, bool]:
    """解析 git status --porcelain=v1 -b 输出为四类文件列表与分支信息。"""
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    conflicted: list[str] = []
    branch = "unknown"
    ahead = 0
    behind = 0
    truncated = False

    def _join(path_text: str) -> str:
        # 重命名记录形如 "old -> new"，两侧都要转成工作区相对路径。
        if " -> " in path_text:
            parts = path_text.split(" -> ", 1)
            return _workspace_join(repo_root_rel, parts[0]) + " -> " + _workspace_join(repo_root_rel, parts[1])
        return _workspace_join(repo_root_rel, path_text)

    for line in str(text or "").splitlines():
        if not line:
            continue
        if line.startswith("## "):
            info = line[3:]
            if info.startswith("HEAD"):
                branch = "detached HEAD"
            else:
                branch = info.split("...", 1)[0].split(" [", 1)[0]
                match = re.search(r"\[(?:ahead (\d+)(?:, behind (\d+))?|behind (\d+))\]", info)
                if match:
                    if match.group(3) is not None:
                        behind = int(match.group(3))
                    else:
                        ahead = int(match.group(1) or 0)
                        behind = int(match.group(2) or 0)
            continue
        if len(line) < 4:
            continue
        xy = line[:2]
        path_text = line[3:]
        if xy == "??":
            untracked.append(_join(path_text))
        elif xy in CONFLICT_PORCELAIN_CODES:
            conflicted.append(_join(path_text))
        else:
            if xy[0] != " " and xy[0] != "?":
                staged.append(_join(path_text))
            if xy[1] != " ":
                unstaged.append(_join(path_text))
        total = len(staged) + len(unstaged) + len(untracked) + len(conflicted)
        if total >= MAX_STATUS_FILES:
            truncated = True
            break
    return staged, unstaged, untracked, conflicted, branch, ahead, behind, truncated


def _parse_branches(text: str) -> list[str]:
    """解析 git branch --list 输出为分支名列表（去掉星号与空白）。"""
    branches: list[str] = []
    for line in str(text or "").splitlines():
        name = line.strip()
        if name.startswith("* "):
            name = name[2:].strip()
        if not name or name.startswith("("):
            continue
        branches.append(name)
        if len(branches) >= MAX_BRANCHES:
            break
    return branches


# ---------------------------------------------------------------- 各 Tool 实现


def _git_status(repository: str = ".", *, runtime: Any | None = None) -> str:
    """git_status：工作区状态、分支、ahead/behind 与四类文件列表。"""
    runtime = runtime or get_git_runtime()
    repo = runtime.resolve_repository(repository)
    result = _run_or_raise(
        runtime,
        ["git", "-C", str(repo.git_root), "status", "--porcelain=v1", "-b"],
        repo,
        writable=False,
        label="读取仓库状态",
    )
    staged, unstaged, untracked, conflicted, branch, ahead, behind, truncated = _parse_status_output(
        result["output"], repo.repo_root_rel
    )
    return json.dumps(
        {
            "ok": True,
            "repo_root": repo.repo_root_rel,
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
            "conflicted": conflicted,
            "truncated": bool(truncated or result["truncated"]),
            "isolation": result["isolation"],
        },
        ensure_ascii=False,
    )


def _git_diff(
    repository: str = ".",
    mode: str = "working",
    paths: list[str] | None = None,
    *,
    runtime: Any | None = None,
) -> str:
    """git_diff：working / staged 差异与统计摘要。"""
    runtime = runtime or get_git_runtime()
    repo = runtime.resolve_repository(repository)
    if mode not in ("working", "staged"):
        raise DailyAgentError("mode 只能是 working 或 staged。")
    validated: list[str] = []
    for raw in paths or []:
        resolved, _ = validate_git_path(str(raw), runtime.workspace_root, repo_root=repo.git_root)
        validated.append(_repo_relative_path(resolved, repo))
    argv = [
        "git",
        "-C",
        str(repo.git_root),
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
    ]
    if mode == "staged":
        argv.append("--cached")
    if validated:
        argv.extend(["--", *validated])
    result = _run_or_raise(runtime, argv, repo, writable=False, label="读取差异")
    stats_argv = ["git", "-C", str(repo.git_root), "diff", "--shortstat"]
    if mode == "staged":
        stats_argv.append("--cached")
    if validated:
        stats_argv.extend(["--", *validated])
    stats_result = _run_or_raise(runtime, stats_argv, repo, writable=False, label="读取差异统计")
    return json.dumps(
        {
            "ok": True,
            "repo_root": repo.repo_root_rel,
            "mode": mode,
            "patch": result["output"],
            "stats": _parse_shortstat(stats_result["output"]),
            "truncated": result["truncated"],
            "isolation": result["isolation"],
        },
        ensure_ascii=False,
    )


def _git_log(
    repository: str = ".",
    max_count: int = DEFAULT_MAX_COUNT,
    revision: str | None = None,
    path: str | None = None,
    *,
    runtime: Any | None = None,
) -> str:
    """git_log：结构化提交列表（hash、短 hash、作者、时间、主题，不含邮箱）。"""
    runtime = runtime or get_git_runtime()
    repo = runtime.resolve_repository(repository)
    count = _clamp_max_count(max_count)
    argv = [
        "git",
        "-C",
        str(repo.git_root),
        "log",
        "-n",
        str(count),
        "--format=" + LOG_FORMAT,
        "--no-decorate",
    ]
    if revision is not None and str(revision).strip():
        argv.append(validate_revision(str(revision)))
    if path is not None and str(path).strip():
        resolved, _ = validate_git_path(str(path), runtime.workspace_root, repo_root=repo.git_root)
        argv.extend(["--", _repo_relative_path(resolved, repo)])
    result = _run_or_raise(runtime, argv, repo, writable=False, label="读取提交历史")
    commits = _parse_log_records(result["output"])
    truncated = result["truncated"]
    # 即使 git 输出未超字节上限，也按字符上限兜底，避免聚合后超出模型上下文。
    used = 0
    kept: list[dict[str, str]] = []
    for commit in commits:
        size = len(commit["subject"]) + len(commit["author"]) + 64
        if used + size > MAX_OUTPUT_CHARS:
            truncated = True
            break
        used += size
        kept.append(commit)
    return json.dumps(
        {
            "ok": True,
            "repo_root": repo.repo_root_rel,
            "commits": kept,
            "truncated": truncated,
            "isolation": result["isolation"],
        },
        ensure_ascii=False,
    )


def _git_show(
    repository: str = ".",
    revision: str = "",
    path: str | None = None,
    *,
    runtime: Any | None = None,
) -> str:
    """git_show：提交元数据 + 受限 patch。"""
    runtime = runtime or get_git_runtime()
    repo = runtime.resolve_repository(repository)
    rev = validate_revision(revision)
    meta_result = _run_or_raise(
        runtime,
        ["git", "-C", str(repo.git_root), "show", "-s", "--format=" + SHOW_META_FORMAT, rev],
        repo,
        writable=False,
        label="读取提交元数据",
    )
    patch_argv = [
        "git",
        "-C",
        str(repo.git_root),
        "show",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--format=",
        rev,
    ]
    if path is not None and str(path).strip():
        resolved, _ = validate_git_path(str(path), runtime.workspace_root, repo_root=repo.git_root)
        patch_argv.extend(["--", _repo_relative_path(resolved, repo)])
    patch_result = _run_or_raise(runtime, patch_argv, repo, writable=False, label="读取提交差异")
    return json.dumps(
        {
            "ok": True,
            "repo_root": repo.repo_root_rel,
            "commit": _parse_show_meta(meta_result["output"]),
            "patch": patch_result["output"],
            "truncated": patch_result["truncated"],
            "isolation": patch_result["isolation"],
        },
        ensure_ascii=False,
    )


def _git_branch_list(
    repository: str = ".",
    include_remote: bool = False,
    *,
    runtime: Any | None = None,
) -> str:
    """git_branch_list：当前分支、本地分支与可选远程跟踪分支。"""
    runtime = runtime or get_git_runtime()
    repo = runtime.resolve_repository(repository)
    current = ""
    symref = runtime.run(
        ["git", "-C", str(repo.git_root), "symbolic-ref", "--short", "-q", "HEAD"],
        repo,
        writable=False,
    )
    if symref["returncode"] == 0:
        current = str(symref["output"]).strip()
    else:
        current = "detached HEAD"
    local_result = _run_or_raise(
        runtime,
        ["git", "-C", str(repo.git_root), "branch", "--list", "--no-color"],
        repo,
        writable=False,
        label="读取本地分支",
    )
    local_branches = _parse_branches(local_result["output"])
    remote_branches: list[str] = []
    remote_truncated = False
    if include_remote:
        remote_result = _run_or_raise(
            runtime,
            ["git", "-C", str(repo.git_root), "branch", "--list", "--no-color", "-r"],
            repo,
            writable=False,
            label="读取远程跟踪分支",
        )
        remote_branches = _parse_branches(remote_result["output"])
        remote_truncated = len(remote_branches) >= MAX_BRANCHES
    return json.dumps(
        {
            "ok": True,
            "repo_root": repo.repo_root_rel,
            "current_branch": current,
            "local_branches": local_branches,
            "remote_branches": remote_branches,
            "truncated": len(local_branches) >= MAX_BRANCHES or remote_truncated,
            "isolation": local_result["isolation"],
        },
        ensure_ascii=False,
    )


def _git_add(
    repository: str = ".",
    paths: list[str] | None = None,
    *,
    runtime: Any | None = None,
) -> str:
    """git_add：只暂存明确给出的工作区相对路径，返回实际暂存文件。"""
    runtime = runtime or get_git_runtime()
    repo = runtime.resolve_repository(repository)
    raw_paths = paths if isinstance(paths, list) else []
    if not raw_paths:
        raise DailyAgentError("paths 必填，至少提供 1 个要暂存的工作区相对路径。")
    if len(raw_paths) > MAX_PATHS:
        raise DailyAgentError(f"paths 最多允许 {MAX_PATHS} 个路径。")
    validated: list[tuple[Path, str]] = []
    repo_relative: list[str] = []
    for raw in raw_paths:
        resolved, workspace_rel = validate_git_path(
            str(raw), runtime.workspace_root, repo_root=repo.git_root
        )
        repo_rel = _repo_relative_path(resolved, repo)
        if not repo_rel:
            raise DailyAgentError("不能暂存 Git 仓库根目录自身。")
        validated.append((resolved, workspace_rel))
        repo_relative.append(repo_rel)
    result = _run_or_raise(
        runtime,
        ["git", "-C", str(repo.git_root), "add", "--", *repo_relative],
        repo,
        writable=True,
        label="暂存文件",
    )
    staged = _staged_files(runtime, repo, include_unmerged=False)
    return json.dumps(
        {
            "ok": True,
            "repo_root": repo.repo_root_rel,
            "requested_paths": [workspace_rel for _, workspace_rel in validated],
            "staged": staged,
            "staged_count": len(staged),
            "truncated": result["truncated"],
            "isolation": result["isolation"],
        },
        ensure_ascii=False,
    )


def _git_commit(repository: str = ".", message: str = "", *, runtime: Any | None = None) -> str:
    """git_commit：确认后提交已暂存变更；hooks/签名/编辑器全部受控关闭。"""
    runtime = runtime or get_git_runtime()
    repo = runtime.resolve_repository(repository)
    msg = validate_message(message)
    staged_before = _staged_files(runtime, repo)
    unmerged = [item for item in staged_before if item["status"] == "U"]
    if unmerged:
        names = "、".join(item["path"] for item in unmerged[:10])
        raise DailyAgentError(
            f"仓库存在未解决的冲突（{names}），请先在本地解决冲突并暂存后再提交；"
            "本批次的 Git 工具不提供 merge/rebase/reset 能力。"
        )
    if not staged_before:
        raise DailyAgentError("没有已暂存的变更，请先用 git_add 暂存文件后再提交。")
    # core.hooksPath 指向 Git 私有 data 内的空目录，连同 --no-verify 双重关闭
    # pre-commit / commit-msg / post-commit 等全部 hooks，避免仓库配置执行任意外部逻辑。
    commit_result = _run_or_raise(
        runtime,
        [
            "git",
            "-C",
            str(repo.git_root),
            "-c",
            f"core.hooksPath={runtime.hooks_off_dir}",
            "commit",
            "-m",
            msg,
            "--no-verify",
            "--no-gpg-sign",
        ],
        repo,
        writable=True,
        label="提交变更",
    )
    head = runtime.run(
        ["git", "-C", str(repo.git_root), "rev-parse", "HEAD"],
        repo,
        writable=False,
    )
    full_hash = str(head["output"]).strip() if head["returncode"] == 0 else ""
    return json.dumps(
        {
            "ok": True,
            "repo_root": repo.repo_root_rel,
            "hash": full_hash,
            "short_hash": full_hash[:7] if full_hash else "",
            "subject": msg,
            "files": [item["path"] for item in staged_before],
            "isolation": commit_result["isolation"],
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------- Agent Tool 构建


def build_git_tools(
    user_id: str = "default",
    confirmed_tool_names: set[str] | None = None,
) -> list[Any]:
    """构建 Agno 原生 Git Tool 列表。

    git_add / git_commit 复用现有确认机制：首次构建 requires_confirmation=True，
    用户确认后 react_runner 会把已确认工具名传回来，同名 Tool 不再重复确认。
    隔离后端不可用时直接返回空列表，Agent 不挂载、Prompt Snapshot 不宣称可用。
    ToolRegistry 在这里延迟导入，避免与 tool_registry 模块形成循环依赖。
    """
    runtime = git_runtime_status()
    if not runtime["available"]:
        logger.warning("Git 工具不可用，不挂载：%s", runtime.get("unavailable_reason"))
        return []
    from agno.tools.function import Function
    from daily_agent.tool_registry import ToolRegistry

    registry = ToolRegistry()
    confirmed = {str(item).replace("-", "_") for item in (confirmed_tool_names or set())}
    runtime = get_git_runtime(user_id)
    entrypoints = {
        "git_status": partial(_git_status, runtime=runtime),
        "git_diff": partial(_git_diff, runtime=runtime),
        "git_log": partial(_git_log, runtime=runtime),
        "git_show": partial(_git_show, runtime=runtime),
        "git_branch_list": partial(_git_branch_list, runtime=runtime),
        "git_add": partial(_git_add, runtime=runtime),
        "git_commit": partial(_git_commit, runtime=runtime),
    }
    tools: list[Any] = []
    for spec in GIT_TOOL_DEFINITIONS:
        name = str(spec["name"])
        tool_id = f"git:{GIT_SOURCE_ID}:{name}"
        if not registry.is_tool_enabled(tool_id):
            continue
        needs_confirmation = bool(spec["requires_confirmation"]) and name not in confirmed
        tools.append(
            Function(
                name=name,
                description=str(spec["description"]),
                parameters=spec["parameters"],
                entrypoint=entrypoints[name],
                skip_entrypoint_processing=True,
                requires_confirmation=needs_confirmation,
            )
        )
    return tools
