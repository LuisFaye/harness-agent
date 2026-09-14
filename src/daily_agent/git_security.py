"""受控 Git 安全层：仓库/路径/revision/提交信息校验与 macOS 文件系统隔离。

本模块与 git_tools.py 拆分开，保证两个文件都低于 1000 行。职责包括：
- 安全常量：revision 字符集合、路径/消息/仓库参数长度上限、敏感路径名单复用；
- 校验函数：resolve_repository / validate_revision / validate_git_path / validate_message；
- 文件系统隔离：git_runtime_status / wrap_git_argv_with_isolation（macOS sandbox-exec）；
- 受控子进程运行器 GitRuntime.run：argv + shell=False、超时、有界输出、私有环境。

隔离边界
--------
- 仅当当前平台存在受支持的隔离后端时 Git Tool 才可用，绝不静默降级为未隔离运行。
- macOS 下用 ``/usr/bin/sandbox-exec`` 包装 argv，profile 默认拒绝：
  * 只读：明确的系统运行目录白名单、实际解析后的可执行程序/运行时根、
    Workspace Root 与 Git 私有 data 目录；
  * 读写：写 Tool 只放行 Git 私有 data 目录与目标仓库的 gitdir / .git 入口，
    不放行工作树；只读 Tool 只允许 Git 私有 data 目录内的临时写入，不改动仓库；
  * 工作区/Git data 下任一级目录中的敏感路径（.env、credentials、私钥、.ssh、
    .aws、.codex、.sb-agent、.git 等，名单与 shell_security._SENSITIVE_PATH_RE
    逐字一致）由 SBPL 显式 deny 优先覆盖；目标仓库对应的 .git 入口与真实 gitdir
    在 deny 之后单独放行，因此 git 能读写自己的元数据，但无法读写其它敏感路径。
  * 不允许任何网络：fetch/push/clone 等网络操作在沙箱内必然失败。
- 子进程使用独立受控环境：只继承 PATH、locale、时区、证书路径等安全键，私有
  HOME/TMPDIR/XDG 位于 Git data 目录内；固定关闭全局/系统 Git 配置、可选锁、
  终端提示、分页器、外部 diff 与编辑器，主进程的模型/数据库/MCP 密钥不会进入。
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError
from daily_agent.paths import AppPaths
from daily_agent.user_workspace import user_workspace_root
from daily_agent.shell_security import (
    SAFE_INHERITED_ENV_KEYS,
    _SENSITIVE_PATH_RE,
    _SYSTEM_READ_DIRS,
    _derive_runtime_read_roots,
    _path_ancestors,
    _resolve_executable,
    _sbpl_path,
    _sbpl_regex,
    _sbpl_sensitive_regexes,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 契约常量

GIT_SOURCE_ID = "workspace-git"

# repository 参数长度上限。
MAX_REPOSITORY_CHARS = 4000

# 单个路径参数长度上限；git_add 一次最多 100 个明确路径。
MAX_PATH_CHARS = 4000
MAX_PATHS = 100

# revision 长度上限与安全字符集合：只允许常见 commit hash、分支/标签名、
# HEAD、父提交后缀（^ / ~）；禁止空格、控制字符、@（reflog）、:（rev:path）、
# {}、..（区间）、反斜杠与任何以 - 开头的选项注入。
MAX_REVISION_CHARS = 200
SAFE_REVISION_RE = re.compile(r"^[0-9A-Za-z._\-/^~]+$")

# 提交信息：1 到 500 字符，必须是单行普通文本，禁止换行、NUL 与控制字符。
MAX_MESSAGE_CHARS = 500

# 有界输出：先按字节上限截断读取，再按字符上限截断展示，防止超大 diff/日志
# 塞爆模型上下文；截断时把 truncated 置为 True。
MAX_OUTPUT_BYTES = 250_000
MAX_OUTPUT_CHARS = 120_000

# 单次 Git 子进程超时（秒）；超时终止整个进程组，不遗留孤儿进程。
DEFAULT_TIMEOUT_SECONDS = 30

# 解析 .git 文件时只允许读取这么小的内容；它只包含一行 "gitdir: <path>"。
MAX_GITDIR_FILE_BYTES = 8192

# 单个状态/分支列表返回条数上限，超过即标记 truncated。
MAX_STATUS_FILES = 500
MAX_BRANCHES = 500

# git status --porcelain=v1 的冲突状态集合：两列都不为正常状态时视为冲突。
CONFLICT_PORCELAIN_CODES = frozenset(
    {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
)


# ---------------------------------------------------------------- 基础判断


def _is_within(root: Path, path: Path) -> bool:
    """判断 path 是否位于 root（或其子目录）内。"""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_sensitive_path(relative: Path) -> bool:
    """相对路径任一分量完整命中敏感名单就拒绝，避免 .env、密钥目录等被读取。"""
    return any(_SENSITIVE_PATH_RE.fullmatch(part) for part in relative.parts)


def _relative_posix(root: Path, path: Path) -> str:
    """返回 path 相对 root 的 posix 路径；相等时返回 "."。"""
    try:
        rel = path.relative_to(root)
    except ValueError as exc:
        raise DailyAgentError("路径超出工作区范围。") from exc
    return "." if str(rel) == "." else rel.as_posix()


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """把用户传参钳制到安全范围；非法值回落默认，避免模型乱传导致越界。"""
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def git_runtime_status() -> dict[str, Any]:
    """返回 Git 文件系统隔离后端可用状态，供 Tool Registry / Agent 挂载决策使用。

    与 Shell 工具一致：只有当前平台存在受支持的隔离后端才返回 available=True；
    否则 7 个 Git Tool 一律 available=False 并给出中文 unavailable_reason，
    绝不静默降级为在主机上裸跑 git。
    """
    if sys.platform != "darwin":
        return {
            "available": False,
            "isolation": None,
            "unavailable_reason": (
                "当前平台没有受支持的文件系统隔离后端（仅 macOS sandbox-exec），Git 工具不可用。"
            ),
        }
    if not os.path.exists("/usr/bin/sandbox-exec"):
        return {
            "available": False,
            "isolation": None,
            "unavailable_reason": (
                "缺少 /usr/bin/sandbox-exec，无法提供受控 Git 执行，Git 工具不可用。"
            ),
        }
    return {
        "available": True,
        "isolation": "macos_sandbox",
        "unavailable_reason": None,
    }

def build_git_environment(git_data_dir: Path) -> dict[str, str]:
    """构造受控 Git 子进程环境。

    - 只继承安全键（PATH、locale、时区、证书路径等），不继承模型/数据库/MCP 密钥；
    - HOME/TMPDIR/XDG 全部指向 Git data 目录内的私有目录；
    - 固定关闭用户全局/系统 Git 配置、可选锁、终端提示、分页器、外部 diff、
      editor 与 gpg 自动签名，避免仓库/用户配置执行任意外部逻辑；
    - LC_ALL 固定为 C，保证输出解析稳定。
    """
    env = {key: os.environ[key] for key in SAFE_INHERITED_ENV_KEYS if key in os.environ}
    home = git_data_dir / "home"
    tmp = git_data_dir / "tmp"
    xdg_config = git_data_dir / "xdg" / "config"
    xdg_cache = git_data_dir / "xdg" / "cache"
    xdg_data = git_data_dir / "xdg" / "data"
    for directory in (home, tmp, xdg_config, xdg_cache, xdg_data, git_data_dir / "hooks-off"):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DailyAgentError(f"无法创建 Git 私有运行目录：{exc}") from exc
    env["HOME"] = str(home)
    env["TMPDIR"] = str(tmp)
    env["XDG_CONFIG_HOME"] = str(xdg_config)
    env["XDG_CACHE_HOME"] = str(xdg_cache)
    env["XDG_DATA_HOME"] = str(xdg_data)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_PAGER"] = "cat"
    env["GIT_EXTERNAL_DIFF"] = ""
    env["GIT_EDITOR"] = "true"
    env["GIT_SEQUENCE_EDITOR"] = "true"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


# ---------------------------------------------------------------- 参数校验


def _validate_no_control_or_newline(text: str, label: str) -> None:
    """拒绝 NUL、换行、回车和其它控制字符，防止注入 git 参数或返回结果。"""
    if "\x00" in text:
        raise DailyAgentError(f"{label}不能包含空字符。")
    if "\n" in text or "\r" in text:
        raise DailyAgentError(f"{label}不能包含换行符，只允许单行输入。")
    if any(ord(ch) < 32 for ch in text):
        raise DailyAgentError(f"{label}不能包含控制字符。")


def validate_revision(text: str) -> str:
    """校验 revision 并返回安全字符串。

    revision 只会作为普通参数放在固定位置（-- 之前、选项之后），但为了纵深防御
    仍严格限制字符集合：不接受以 - 开头（避免被当成选项）、不接受 @/{}（reflog
    shell-like 选择器）、..（区间）、:（rev:path）、空白与控制字符。
    """
    if not isinstance(text, str):
        raise DailyAgentError("revision 必须是字符串。")
    rev = text.strip()
    if not rev:
        raise DailyAgentError("revision 不能为空。")
    if len(rev) > MAX_REVISION_CHARS:
        raise DailyAgentError(f"revision 过长（{len(rev)} 字符），上限 {MAX_REVISION_CHARS} 字符。")
    _validate_no_control_or_newline(rev, "revision")
    if rev.startswith("-"):
        raise DailyAgentError("revision 不能以 - 开头，避免被解析成命令行选项。")
    if ".." in rev:
        raise DailyAgentError("revision 不支持 .. 区间语法。")
    if "@" in rev or "{" in rev or "}" in rev or ":" in rev:
        raise DailyAgentError("revision 不支持 @/{} / : 等选择器或路径语法（含 reflog）。")
    if not SAFE_REVISION_RE.fullmatch(rev):
        raise DailyAgentError(
            "revision 包含不允许的字符，只接受字母、数字、._- / ^ ~ 与常见分支/标签名。"
        )
    return rev


def validate_message(text: str) -> str:
    """校验提交信息：1 到 500 字符、单行、无控制字符，返回去除首尾空白后的原文。"""
    if not isinstance(text, str):
        raise DailyAgentError("message 必须是字符串。")
    message = text.strip()
    if not message:
        raise DailyAgentError("提交信息不能为空。")
    if len(message) > MAX_MESSAGE_CHARS:
        raise DailyAgentError(
            f"提交信息过长（{len(message)} 字符），上限 {MAX_MESSAGE_CHARS} 字符。"
        )
    _validate_no_control_or_newline(message, "提交信息")
    return message


def validate_git_path(
    text: str,
    workspace_root: Path,
    *,
    repo_root: Path | None = None,
) -> tuple[Path, str]:
    """校验工作区内的 Git 路径参数，返回 (解析后的绝对路径, 相对工作区 posix 路径)。

    拒绝：空路径、越界绝对路径、..、符号链接逃逸、NUL/换行、以 - 开头的路径、
    Git pathspec magic（:(...) 等）、反斜杠、通配符，以及任一分量命中敏感名单的路径。
    repo_root 给定时额外要求路径必须位于目标 Git 仓库内（git_add / git_diff 等）。
    """
    if not isinstance(text, str):
        raise DailyAgentError("路径必须是字符串。")
    path = text.strip()
    if not path:
        raise DailyAgentError("路径不能为空。")
    if len(path) > MAX_PATH_CHARS:
        raise DailyAgentError(f"路径过长（{len(path)} 字符），上限 {MAX_PATH_CHARS} 字符。")
    _validate_no_control_or_newline(path, "路径")
    if path.startswith("-"):
        raise DailyAgentError("路径不能以 - 开头。")
    if path.startswith(":"):
        raise DailyAgentError("不支持 Git pathspec magic（:(...) 形式）。")
    if "\\" in path:
        raise DailyAgentError("路径不能包含反斜杠。")
    if any(ch in path for ch in "*?[]"):
        raise DailyAgentError("路径不能包含通配符，必须指定明确的文件或目录。")
    candidate = Path(path)
    if ".." in candidate.parts:
        raise DailyAgentError("路径不能包含 ..，防止越出工作区。")
    absolute = candidate if candidate.is_absolute() else workspace_root / candidate
    try:
        resolved = absolute.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise DailyAgentError(f"路径无法解析：{exc}") from exc
    if not _is_within(workspace_root, resolved):
        raise DailyAgentError("路径超出工作区范围。")
    relative = resolved.relative_to(workspace_root)
    if _is_sensitive_path(relative):
        raise DailyAgentError("路径指向敏感文件或目录（.env、凭据、私钥、.git 等），已拒绝。")
    if repo_root is not None and not _is_within(repo_root, resolved):
        raise DailyAgentError("路径不在目标 Git 仓库内，无法操作。")
    return resolved, _relative_posix(workspace_root, resolved)


def _validate_repository_raw(raw: str, workspace_root: Path) -> tuple[Path, Path]:
    """校验 repository 参数并解析出目录，返回 (解析后绝对目录, 相对工作区路径)。"""
    if not isinstance(raw, str):
        raise DailyAgentError("repository 必须是字符串。")
    text = raw.strip()
    if not text:
        raise DailyAgentError("repository 不能为空。")
    if len(text) > MAX_REPOSITORY_CHARS:
        raise DailyAgentError(f"repository 过长（{len(text)} 字符），上限 {MAX_REPOSITORY_CHARS} 字符。")
    _validate_no_control_or_newline(text, "repository")
    candidate = Path(text)
    if ".." in candidate.parts:
        raise DailyAgentError("repository 不能包含 ..，防止越出工作区。")
    absolute = candidate if candidate.is_absolute() else workspace_root / candidate
    try:
        resolved = absolute.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise DailyAgentError(f"repository 无法解析：{exc}") from exc
    if not _is_within(workspace_root, resolved):
        raise DailyAgentError("repository 超出工作区范围，只接受工作区内的 Git 仓库。")
    relative = resolved.relative_to(workspace_root)
    if _is_sensitive_path(relative):
        raise DailyAgentError("repository 指向敏感目录，已拒绝。")
    if not resolved.is_dir():
        raise DailyAgentError(f"repository 不存在或不是目录：{text}")
    return resolved, relative


# ---------------------------------------------------------------- 仓库信息


@dataclass(frozen=True)
class RepoInfo:
    """一次解析出的目标仓库信息：git 根、gitdir 与各自相对工作区的 posix 路径。"""

    git_root: Path
    git_dir: Path
    repo_root_rel: str
    git_dir_rel: str


class GitRuntime:
    """Git 工具运行时：持有工作区根与 Git 私有 data 目录，提供解析与受控执行。"""

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        paths = AppPaths.discover(workspace_root=workspace_root, data_dir=data_dir)
        self.workspace_root = paths.workspace_root
        self.git_data_dir = paths.data_dir / "git"
        self.hooks_off_dir = self.git_data_dir / "hooks-off"

    # ---------------- repository 解析 ----------------

    def resolve_repository(self, raw: str = ".") -> RepoInfo:
        """解析 repository 参数为受信仓库信息，任何越权情况都抛出中文错误。

        流程：
        1. 校验 repository 参数并解析出工作区内目录；
        2. 探测该目录的 .git 入口：目录形式直接作为候选 gitdir，文件形式
           读取其中的 "gitdir:" 指向并校验必须位于工作区内；
        3. 用候选 gitdir 的隔离 profile 运行 git rev-parse，取得真实
           git root / gitdir / 是否裸仓库；
        4. 最终 git root 与 gitdir 都必须位于工作区内，裸仓库明确拒绝。
        """
        resolved, _ = _validate_repository_raw(raw, self.workspace_root)
        git_entry = resolved / ".git"
        provisional_git_dir: Path | None = None
        if git_entry.is_dir():
            provisional_git_dir = git_entry
        elif git_entry.is_file():
            provisional_git_dir = self._parse_gitdir_file(git_entry, resolved)
        provisional = RepoInfo(
            git_root=resolved,
            git_dir=provisional_git_dir or git_entry,
            repo_root_rel=_relative_posix(self.workspace_root, resolved),
            git_dir_rel="",
        )
        result = self.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel", "--absolute-git-dir", "--is-bare-repository"],
            provisional,
            writable=False,
        )
        if result["returncode"] != 0:
            detail = str(result["output"]).strip() or "未知错误"
            raise DailyAgentError(f"不是可用的 Git 仓库（{detail[:200]}）。")
        lines = [line for line in str(result["output"]).splitlines() if line.strip()]
        if len(lines) < 3:
            raise DailyAgentError("无法从 git rev-parse 取得完整仓库信息。")
        try:
            git_root = Path(lines[0].strip()).resolve(strict=False)
            git_dir = Path(lines[1].strip()).resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise DailyAgentError(f"解析 Git 仓库路径失败：{exc}") from exc
        is_bare = lines[2].strip() == "true"
        if is_bare:
            raise DailyAgentError("暂不支持裸仓库，请使用普通工作树仓库。")
        if not _is_within(self.workspace_root, git_root):
            raise DailyAgentError("Git 根目录超出工作区范围，已拒绝。")
        if not _is_within(self.workspace_root, git_dir):
            raise DailyAgentError(
                "Git 元数据目录（gitdir）位于工作区之外（可能是外置 gitdir 或 linked worktree），"
                "首版不支持，已拒绝。"
            )
        return RepoInfo(
            git_root=git_root,
            git_dir=git_dir,
            repo_root_rel=_relative_posix(self.workspace_root, git_root),
            git_dir_rel=_relative_posix(self.workspace_root, git_dir),
        )

    def _parse_gitdir_file(self, entry: Path, worktree: Path) -> Path:
        """解析工作树里的 .git 文件（linked worktree / submodule 场景）中的 gitdir 指向。"""
        try:
            data = entry.read_bytes()
        except OSError as exc:
            raise DailyAgentError(f"无法读取工作树的 .git 文件：{exc}") from exc
        if len(data) > MAX_GITDIR_FILE_BYTES:
            raise DailyAgentError("工作树的 .git 文件异常过大，已拒绝。")
        text = data.decode("utf-8", errors="replace")
        target: Path | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("gitdir:"):
                target = Path(stripped.split(":", 1)[1].strip())
                break
        if target is None or not str(target):
            raise DailyAgentError("工作树的 .git 文件缺少有效 gitdir 指向，已拒绝。")
        if not target.is_absolute():
            target = worktree / target
        try:
            real = target.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise DailyAgentError(f"gitdir 指向无法解析：{exc}") from exc
        if not _is_within(self.workspace_root, real):
            raise DailyAgentError(
                "Git 元数据目录（gitdir）位于工作区之外（可能是外置 gitdir 或 linked worktree），"
                "首版不支持，已拒绝。"
            )
        return real

    # ---------------- 受控执行 ----------------

    def run(
        self,
        argv: list[str],
        repo: RepoInfo,
        *,
        writable: bool,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """在文件系统隔离下运行一次 git 命令，返回有界结果。

        - argv + shell=False，绝不经过 shell 解释器；
        - 设置超时并终止整个进程组，避免孤儿进程；
        - 输出先按字节上限、再按字符上限截断，truncated 反映是否丢过内容；
        - 只读工具（writable=False）不允许写工作区与 gitdir，只有 Git 私有
          data 目录可写（临时文件）；写工具只放行 Git 私有 data 目录与目标仓库
          gitdir / .git 入口写入，不写工作树。
        """
        env = build_git_environment(self.git_data_dir)
        launch_argv, isolation = wrap_git_argv_with_isolation(
            argv,
            self.workspace_root,
            self.git_data_dir,
            repo.git_root,
            repo.git_dir,
            writable=writable,
        )
        try:
            proc = subprocess.Popen(
                launch_argv,
                cwd=str(repo.git_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            raise DailyAgentError(f"无法启动 git 进程：{exc}") from exc

        chunks: list[bytes] = []
        total = 0
        dropped_bytes = False
        reader_done = threading.Event()

        def _reader() -> None:
            """后台读取线程：有界读取 stdout（stderr 合并进同一管道），EOF 后置事件。"""
            nonlocal dropped_bytes, total
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    if total < MAX_OUTPUT_BYTES:
                        room = MAX_OUTPUT_BYTES - total
                        chunks.append(chunk[:room])
                        total += min(len(chunk), room)
                        if len(chunk) > room:
                            dropped_bytes = True
                    else:
                        dropped_bytes = True
            except Exception:
                logger.exception("Git 输出读取线程异常退出")
            finally:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                reader_done.set()

        threading.Thread(target=_reader, daemon=True, name="git-reader").start()
        timed_out = False
        try:
            proc.wait(timeout=max(1, int(timeout)))
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_group(proc)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._kill_group(proc)
                proc.wait()
        # 有界等待读取线程排空管道，避免尾部输出丢失。
        reader_done.wait(timeout=2)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        truncated_chars = len(text) > MAX_OUTPUT_CHARS
        if truncated_chars:
            text = text[:MAX_OUTPUT_CHARS] + "\n……（输出已截断，请缩小范围后重试）"
        return {
            "returncode": proc.returncode,
            "output": text,
            "truncated": bool(dropped_bytes or truncated_chars),
            "timed_out": timed_out,
            "isolation": isolation,
        }

    @staticmethod
    def _terminate_group(proc: subprocess.Popen) -> None:
        """向整个进程组发送 TERM（子进程独立 session，pgid 等于 pid）。"""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        """TERM 宽限后仍存活则 KILL，保证超时后不留孤儿。"""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


# ---------------------------------------------------------------- 沙箱 profile

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _build_git_sandbox_profile(
    argv: list[str],
    workspace_root: Path,
    git_data_dir: Path,
    git_root: Path,
    git_dir: Path,
    *,
    writable: bool,
) -> str:
    """构造 sandbox-exec SBPL profile：默认拒绝，只放行受控范围。

    顺序即优先级：先放行系统只读目录/工作区/Git data 的读取，再追加敏感路径
    deny（包含 .git），最后放行目标仓库的 .git 入口与真实 gitdir——SBPL 对
    同一操作按最后命中规则生效，因此 git 只能读写自己仓库的元数据，其它敏感
    路径（.env、凭据、私钥、.ssh/.aws/.codex/.sb-agent 等）仍被 deny 覆盖。
    写 Tool 的写放行只包含 Git 私有 data 目录与目标仓库 gitdir / .git 入口，
    不放行 Workspace Root 普通文件与目录：git_add / git_commit 只写 git 自身
    元数据，工作树内容由外部进程写入，沙箱不为此放行写权限。
    所有路径统一用 realpath，避免 /tmp 这类符号链接造成沙箱匹配不到。
    """
    rw = Path(os.path.realpath(workspace_root))
    rd = Path(os.path.realpath(git_data_dir))
    rg = Path(os.path.realpath(git_dir))
    git_entry = Path(os.path.realpath(git_root / ".git"))
    read_roots = set(_SYSTEM_READ_DIRS)
    real = _resolve_executable(str(argv[0]))
    if real is not None:
        read_roots.update(_derive_runtime_read_roots(real, rw, rd))
    read_roots.update({str(rw), str(rd)})
    metadata_literals = sorted(set(_path_ancestors(rw)) | set(_path_ancestors(rd)))
    lines = [
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(allow process*)",
        # Git 工具一律不允许网络：fetch/push/clone 等操作在沙箱内必然失败。
        "(deny network*)",
        "(allow file-read* "
        + " ".join(f"(subpath {_sbpl_path(path)})" for path in sorted(read_roots))
        + ")",
        "(allow file-read-metadata "
        + " ".join(f"(literal {_sbpl_path(path)})" for path in metadata_literals)
        + ")",
    ]
    if writable:
        # 写 Tool（git_add / git_commit）只放行 Git 私有 data 目录与目标仓库的
        # gitdir / .git 入口写入，不放行工作树：仓库内恶意 clean filter 等外部
        # 进程无法借 git 子进程修改工作区其它文件。
        lines.append(
            "(allow file-write* "
            + f"(subpath {_sbpl_path(str(rd))}) "
            + f"(subpath {_sbpl_path(str(rg))}) "
            + f"(subpath {_sbpl_path(str(git_entry))}))"
        )
    else:
        # 只读 Tool 只允许 Git 私有 data 目录内的临时写入，不改动仓库。
        lines.append(f"(allow file-write* (subpath {_sbpl_path(str(rd))}))")
    # 敏感路径 deny 必须放在通用 allow 之后才生效；名单与 Shell 工具一致。
    for root in (rw, rd):
        for pattern in _sbpl_sensitive_regexes(root):
            regex = _sbpl_regex(pattern)
            lines.append(f"(deny file-read* (regex {regex}))")
            lines.append(f"(deny file-write* (regex {regex}))")
    # 目标仓库的 .git 入口（目录或文件）与真实 gitdir 最后放行，覆盖上面的 deny。
    lines.append(f"(allow file-read* (subpath {_sbpl_path(str(git_entry))}))")
    lines.append(f"(allow file-read* (subpath {_sbpl_path(str(rg))}))")
    if writable:
        lines.append(f"(allow file-write* (subpath {_sbpl_path(str(rg))}))")
    return "\n".join(lines)


def wrap_git_argv_with_isolation(
    argv: list[str],
    workspace_root: Path,
    git_data_dir: Path,
    git_root: Path,
    git_dir: Path,
    *,
    writable: bool,
) -> tuple[list[str], str]:
    """把 argv 包装成受文件系统隔离保护的启动参数，返回 (wrapped_argv, isolation 标识)。

    隔离后端不可用时直接抛错，禁止无隔离运行 git；包装只是 argv 前置，不引入 shell。
    """
    runtime = git_runtime_status()
    if not runtime["available"]:
        raise DailyAgentError(
            str(runtime["unavailable_reason"] or "文件系统隔离后端不可用，无法执行 Git 命令。")
        )
    profile = _build_git_sandbox_profile(
        argv,
        workspace_root,
        git_data_dir,
        git_root,
        git_dir,
        writable=writable,
    )
    return [_SANDBOX_EXEC, "-p", profile, *argv], str(runtime["isolation"])


# ---------------------------------------------------------------- 模块级单例

_git_runtimes: dict[str, GitRuntime] = {}
_git_runtime_lock = threading.Lock()


def get_git_runtime(user_id: str = "default") -> GitRuntime:
    """按用户返回 GitRuntime，仓库根与 Git 私有临时目录都互相隔离。"""
    owner = str(user_id or "default")
    with _git_runtime_lock:
        runtime = _git_runtimes.get(owner)
        if runtime is None:
            paths = AppPaths.discover()
            workspace_root = user_workspace_root(owner, paths=paths)
            data_dir = paths.data_dir if owner == "default" else paths.data_dir / "users" / owner / "runtime"
            runtime = GitRuntime(workspace_root=workspace_root, data_dir=data_dir)
            _git_runtimes[owner] = runtime
        return runtime
