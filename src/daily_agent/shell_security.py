"""受控 Shell 安全层：命令/参数/路径/环境校验与 macOS 文件系统隔离。

本模块与 shell_tools.py 拆分开，保证两个文件都低于 1000 行。职责包括：
- 安全常量：敏感字样、敏感路径名单、永久拒绝命令、内联代码选项；
- 校验函数：validate_command / validate_arg_paths / resolve_cwd / validate_environment；
- 文件系统隔离：shell_runtime_status / wrap_argv_with_isolation（macOS sandbox-exec）。

隔离边界
--------
- 仅当当前平台存在受支持的文件系统隔离后端时才允许执行，绝不静默降级为
  "private_process" 仍标记可用；不可用平台返回 available=False 和中文原因。
- macOS 下用 ``/usr/bin/sandbox-exec`` 包装 argv，sandbox 只放行：
  * 只读：明确的系统运行目录白名单（/usr、/bin、/sbin、/System/Library、/Library、
    /etc、/private/etc、/dev），以及实际解析后的可执行程序/运行时根
    （例如 Python.framework、nvm 的 node 版本目录），不放行整个用户 HOME；
  * 读写：Workspace Root 与 Shell 私有 data 目录，但工作区/data 下任一级目录中
    的敏感路径（.env、credentials、config.toml、私钥/证书、.git 等，名单与
    ``_SENSITIVE_PATH_RE`` 一致）由 SBPL 显式 deny 优先覆盖，脚本在进程内
    硬编码访问也无法绕过；
  其余路径（/Users/...、/private/var/... 下的用户与临时文件）默认拒绝。
- Popen 仍必须 shell=False；sandbox 包装只是 argv 前置，不重新引入 shell。
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 契约/环境常量

# command 长度：1 到 4000 字符。
MAX_COMMAND_CHARS = 4000

# environment 参数的键/值/条目数上限。
MAX_ENV_KEY_CHARS = 64
MAX_ENV_VALUE_CHARS = 200
MAX_ENV_ITEMS = 8

# 子进程默认只继承这些父进程环境键：路径、locale、时区、证书等非敏感配置。
SAFE_INHERITED_ENV_KEYS = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LC_COLLATE",
        "LC_MONETARY",
        "LC_NUMERIC",
        "LC_TIME",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    }
)

# shell_execute 的 environment 参数只允许这些明确非敏感键。
ALLOWED_ENVIRONMENT_KEYS = frozenset(
    {"CI", "NO_COLOR", "FORCE_COLOR", "PYTHONUNBUFFERED", "NODE_ENV", "TZ", "LANG", "LC_ALL"}
)

# ---------------------------------------------------------------- 敏感字样/路径

# 命令或参数出现这些敏感字样时直接拒绝执行（防泄露；同时作为返回脱敏的匹配规则）。
SENSITIVE_WORD_RE = re.compile(
    r"(?:password|passwd|token|secret|authorization|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|bearer)",
    re.IGNORECASE,
)

# 与工作区文件工具一致的敏感路径/文件名名单：参数任一分量完整命中即拒绝，
# 覆盖 .ENV、SECRET.PEM 等变体，并拦截常见凭据文件。
_SENSITIVE_PATH_RE = re.compile(
    r"^(?:\.git|\.ssh|\.aws|\.codex|\.sb-agent|\.env|\.env\..+|id_rsa|id_ed25519|"
    r"credentials|credentials\..+|\.netrc|\.npmrc|\.pypirc|config\.toml|"
    r"secret|secret\..+|secrets|secrets\..+|.+\.pem|.+\.key|.+\.p12|.+\.pfx)$",
    re.IGNORECASE,
)

# SBPL deny 名单直接复用 _SENSITIVE_PATH_RE 的备选分支（整体形如 ^(?:A|B|C)$），
# 保证真实沙箱拒绝与 Python 参数预检逐字一致；名单结构变化会让模块在导入时报错，
# 强制两处同步，避免只改一边造成语义漂移。
if _SENSITIVE_PATH_RE.pattern.startswith("^(?:") and _SENSITIVE_PATH_RE.pattern.endswith(")$"):
    _SENSITIVE_PATH_SBPL_FRAGMENTS: tuple[str, ...] = tuple(_SENSITIVE_PATH_RE.pattern[4:-2].split("|"))
else:
    raise AssertionError("_SENSITIVE_PATH_RE 结构变化，请同步 SBPL 敏感路径 deny 名单解析")

# 这些扩展名意味着该参数极可能指向文件路径，需要做敏感分量检查；
# 不在名单内的带点号普通值（版本号、域名、flag 值等）不会被误判成路径。
_PATH_LIKE_SUFFIXES = frozenset(
    {".pem", ".key", ".p12", ".pfx", ".toml", ".env", ".netrc", ".npmrc", ".pypirc"}
)

# 命令中不允许出现的控制字符/运算符；由于不经 shell 执行，出现即视为拼接或重定向企图。
_FORBIDDEN_CONTROL_CHARS = frozenset(";|&><`")

# ---------------------------------------------------------------- 拒绝命令名单

# 永久拒绝的命令名（argv[0] 的文件名完整命中即拒绝，大小写不敏感）。
DENIED_COMMAND_NAMES = frozenset(
    {
        # 提权
        "sudo", "su", "doas",
        # 删除与系统破坏
        "rm", "rmdir", "unlink", "dd", "fdisk", "sfdisk", "parted", "wipefs",
        "shutdown", "reboot", "halt", "poweroff", "diskutil",
        "mount", "umount", "init", "systemctl", "service",
        # 进程/后台与计划任务
        "kill", "pkill", "killall", "nohup", "setsid", "disown", "at", "crontab",
        # shell 解释器（任何形式都不允许经解释器执行）
        "sh", "bash", "zsh", "fish", "ksh", "csh", "tcsh", "dash", "ash",
        "powershell", "pwsh", "cmd", "cmd.exe", "powershell.exe",
        # shell 内建/会话控制
        "cd", "export", "unset", "alias", "source", "eval",
    }
)

# mkfs 及 mkfs.* 系列命令统一拒绝。
_MKFS_RE = re.compile(r"^mkfs(?:\..+)?$", re.IGNORECASE)

# ---------------------------------------------------------------- 内联代码拦截

# 这些解释器不允许内联任意代码（python -c / node -e / perl -e / php -r 等），
# 但允许执行脚本文件或 -m / -v / --version 等常规参数。
_INLINE_CODE_INTERPRETERS = frozenset({"python", "python2", "python3", "node", "perl", "ruby", "php", "lua"})

# 每个解释器的内联代码选项：必须是参数序列中 "--" 之前出现的完整选项。
# Python 没有标准 --command 长选项，不虚构；Ruby/Perl 的 -e、Node 的 --eval 均覆盖。
_INLINE_CODE_OPTIONS: dict[str, frozenset[str]] = {
    "python": frozenset({"-c"}),
    "python2": frozenset({"-c"}),
    "python3": frozenset({"-c"}),
    "node": frozenset({"-e", "-p", "--eval", "--print"}),
    "perl": frozenset({"-e", "-E"}),
    "ruby": frozenset({"-e", "--eval"}),
    "php": frozenset({"-r", "-R"}),
    "lua": frozenset({"-e"}),
}

# 允许写成 "--eval=..." / "--print=..." 的长选项前缀。
_INLINE_CODE_LONG_EQUALS: dict[str, tuple[str, ...]] = {
    "node": ("--eval", "--print"),
    "ruby": ("--eval",),
}

# 短选项打包：python 的 -uc / -Bc 等价于 -u -c / -B -c，必须拒绝；
# 但 -Werror 是 -W 带参数，不是打包，不能误判。perl 的 -we / -pe 同理拒绝，
# 而 -Mmodule 是 -M 带参数，不属于打包。
_INLINE_CODE_BUNDLE_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"^-[bBdEhiIOqsStuvVx]+c$"),
    "python2": re.compile(r"^-[bBdEhiIOqsStuvVx]+c$"),
    "python3": re.compile(r"^-[bBdEhiIOqsStuvVx]+c$"),
    "perl": re.compile(r"^-[a-zA-Z]{1,3}[eE]$"),
}

# perl 打包判定里明确带参数的选项字符：-M/-I/-d/-D/-l 等，命中则跳过打包检查。
_INLINE_CODE_BUNDLE_EXCLUDE: dict[str, frozenset[str]] = {
    "perl": frozenset("MI"),
}

# ---------------------------------------------------------------- 基础工具


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


def _looks_like_path(value: str) -> bool:
    """判断一个参数值是否疑似文件系统路径；只有疑似路径才做敏感分量检查。

    普通非路径参数、URL 和 flag 不能因为带点号就被误判成路径：
    - 含 "/" 或以 "." 开头的一律视为路径候选；
    - 裸文件名只有命中敏感名单或敏感扩展名才算路径候选；
    - 版本号（v1.2.3）、域名（example.com）、普通 flag 值不在名单内，保持兼容。
    """
    if "/" in value or value.startswith("."):
        return True
    name = Path(value).name
    if _SENSITIVE_PATH_RE.fullmatch(name):
        return True
    return Path(value).suffix.lower() in _PATH_LIKE_SUFFIXES


def redact_command(command: str) -> str:
    """返回给模型的 command 字段统一脱敏；敏感命令在启动前已被拒绝，这里是纵深防御。"""
    return SENSITIVE_WORD_RE.sub("***", str(command))


# ---------------------------------------------------------------- 命令与参数安全校验


def validate_command(command: str) -> list[str]:
    """校验命令字符串并解析成参数数组；任何不安全输入都以明确中文错误拒绝。"""
    if not isinstance(command, str):
        raise DailyAgentError("command 必须是字符串。")
    text = command.strip()
    if not text:
        raise DailyAgentError("command 不能为空。")
    if len(text) > MAX_COMMAND_CHARS:
        raise DailyAgentError(f"command 过长（{len(text)} 字符），上限 {MAX_COMMAND_CHARS} 字符。")
    if "\x00" in command or "\n" in command or "\r" in command:
        raise DailyAgentError("command 不能包含换行或空字符，只允许单条命令。")
    if any(ch in command for ch in _FORBIDDEN_CONTROL_CHARS):
        raise DailyAgentError("command 包含管道、重定向、控制运算符或后台符号，已拒绝。")
    if "$(" in command or "${" in command or "`" in command:
        raise DailyAgentError("command 包含命令替换或参数展开语法，已拒绝。")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise DailyAgentError(f"command 引号不完整，无法安全解析：{exc}") from exc
    if not argv:
        raise DailyAgentError("command 解析后为空。")
    for token in argv:
        if SENSITIVE_WORD_RE.search(token):
            raise DailyAgentError(
                "command 参数包含敏感字样（password/token/secret/authorization/api_key 等），已拒绝执行。"
            )
    program = Path(argv[0]).name.lower()
    if program in DENIED_COMMAND_NAMES or _MKFS_RE.match(program):
        raise DailyAgentError(f"命令「{argv[0]}」在永久拒绝名单中，不能通过 Shell 工具执行。")
    if program in _INLINE_CODE_INTERPRETERS:
        _reject_inline_code(argv, program)
    return argv


def _reject_inline_code(argv: list[str], program: str) -> None:
    """检查解释器参数序列中 "--" 之前的所有选项，而不是只看 argv[1]。

    覆盖短选项、长选项（node --eval）、长选项 "=" 形式以及常见短选项打包
    （python -uc / perl -we），确保 python3 -u -c、python3 -B -c、
    node --trace-warnings --eval 等一律被拒绝。
    """
    options = _INLINE_CODE_OPTIONS.get(program, frozenset())
    long_equals = _INLINE_CODE_LONG_EQUALS.get(program, ())
    bundle_pattern = _INLINE_CODE_BUNDLE_PATTERNS.get(program)
    bundle_exclude = _INLINE_CODE_BUNDLE_EXCLUDE.get(program, frozenset())
    for token in argv[1:]:
        if token == "--":
            # "--" 之后是脚本/命令自身参数，不再是解释器选项。
            break
        if token in options:
            raise DailyAgentError("不允许用解释器执行内联代码（如 python -c / node -e）。")
        if token.startswith("-") and not token.startswith("--"):
            if bundle_pattern is not None and bundle_pattern.fullmatch(token):
                if not any(ch in bundle_exclude for ch in token[1:-1]):
                    raise DailyAgentError("不允许用解释器执行内联代码（如 python -uc / perl -we）。")
            continue
        for prefix in long_equals:
            if token.startswith(prefix + "="):
                raise DailyAgentError("不允许用解释器执行内联代码（如 node --eval）。")


def validate_arg_paths(argv: list[str], root: Path) -> None:
    """检查命令参数里的路径：拒绝 ..、工作区外绝对路径和敏感文件/目录。

    所有相对参数（包括裸文件名 config.toml、credentials 等）都会先判断是否疑似路径，
    命中敏感名单即拒绝；--config=config.toml 这类 "--key=value" 形式单独取 value 检查。
    普通非路径参数、URL、版本号和普通 flag 不受影响。
    """
    for token in argv[1:]:
        # --key=value 形式的值单独按路径规则检查，覆盖 .env 等敏感值。
        value = token.split("=", 1)[1] if "=" in token else token
        if not value:
            continue
        candidate = Path(value)
        if ".." in candidate.parts:
            raise DailyAgentError(f"参数「{token}」包含 ..，不允许越出工作区。")
        if candidate.is_absolute():
            try:
                resolved = candidate.resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                raise DailyAgentError(f"参数「{token}」无法解析：{exc}") from exc
            if not _is_within(root, resolved):
                raise DailyAgentError(f"参数「{token}」指向工作区之外的绝对路径，已拒绝。")
            if _is_sensitive_path(resolved.relative_to(root)):
                raise DailyAgentError(f"参数「{token}」指向敏感文件或目录，已拒绝。")
        elif _looks_like_path(value):
            # 相对/裸参数只要疑似路径就检查敏感分量；普通 flag、URL、版本号跳过。
            if _is_sensitive_path(candidate):
                raise DailyAgentError(f"参数「{token}」指向敏感文件或目录，已拒绝。")


def resolve_cwd(root: Path, raw: str) -> tuple[Path, str]:
    """把 cwd 解析成工作区内真实目录，返回 (绝对目录, 相对工作区的 posix 路径)。"""
    text = str(raw or "").strip() or "."
    candidate = Path(text)
    if ".." in candidate.parts:
        raise DailyAgentError("cwd 不能包含 ..，防止越出工作区。")
    absolute = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = absolute.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise DailyAgentError(f"cwd 无法解析：{exc}") from exc
    if not _is_within(root, resolved):
        raise DailyAgentError("cwd 超出工作区范围。")
    relative = resolved.relative_to(root)
    if _is_sensitive_path(relative):
        raise DailyAgentError("cwd 指向敏感目录，已拒绝。")
    if not resolved.is_dir():
        raise DailyAgentError(f"cwd 不存在或不是目录：{text}")
    return resolved, relative.as_posix()


def validate_environment(environment: Any) -> dict[str, str]:
    """校验 environment 参数：只允许白名单键，键值长度和条目数都有上限。"""
    if environment is None:
        return {}
    if not isinstance(environment, dict):
        raise DailyAgentError("environment 必须是对象。")
    if len(environment) > MAX_ENV_ITEMS:
        raise DailyAgentError(f"environment 最多允许 {MAX_ENV_ITEMS} 个变量。")
    result: dict[str, str] = {}
    for key, value in environment.items():
        key_text = str(key)
        if key_text not in ALLOWED_ENVIRONMENT_KEYS:
            raise DailyAgentError(f"环境变量「{key_text}」不在白名单内，已拒绝。")
        if len(key_text) > MAX_ENV_KEY_CHARS:
            raise DailyAgentError("环境变量名过长。")
        value_text = str(value)
        if len(value_text) > MAX_ENV_VALUE_CHARS:
            raise DailyAgentError(f"环境变量「{key_text}」的值过长。")
        result[key_text] = value_text
    return result


# ---------------------------------------------------------------- 文件系统隔离

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# 只读系统运行目录白名单：最小明确集合，不含用户 HOME 与 /private/var 用户数据。
_SYSTEM_READ_DIRS = (
    "/usr",
    "/bin",
    "/sbin",
    "/System/Library",
    "/Library",
    "/etc",
    "/private/etc",
    "/dev",
)


def shell_runtime_status() -> dict[str, Any]:
    """返回 Shell 文件系统隔离后端可用状态，供 Tool Registry / Agent 挂载决策使用。

    只有当前平台存在受支持的隔离后端才返回 available=True；否则三个 Shell Tool
    一律 available=False 并给出中文 unavailable_reason，绝不静默降级为未隔离执行。
    """
    if sys.platform != "darwin":
        return {
            "available": False,
            "isolation": None,
            "unavailable_reason": "当前平台没有受支持的文件系统隔离后端（仅 macOS sandbox-exec），Shell 工具不可用。",
        }
    if not os.path.exists(_SANDBOX_EXEC):
        return {
            "available": False,
            "isolation": None,
            "unavailable_reason": f"缺少 {_SANDBOX_EXEC}，无法提供受控工作区 Shell，Shell 工具不可用。",
        }
    return {
        "available": True,
        "isolation": "macos_sandbox",
        "unavailable_reason": None,
    }


def _resolve_executable(argv0: str) -> Path | None:
    """按子进程相同的 PATH 解析 argv[0] 的真实可执行文件路径。"""
    if "/" in argv0:
        candidate = Path(argv0)
        if candidate.exists():
            return candidate.resolve()
        return None
    found = shutil.which(argv0)
    if not found:
        return None
    return Path(found).resolve()


def _derive_runtime_read_roots(real: Path, workspace: Path, data_dir: Path) -> set[str]:
    """从解析后的可执行文件推导运行时只读根：framework 根 + 用户目录下的工具链根。

    - 例如 /Library/Frameworks/Python.framework/... 允许整个 framework 根；
    - 例如 ~/.nvm/versions/node/v24.13.0/... 只允许该版本目录，不放行整个 HOME；
    - 工具链根解析不出来时退化为可执行文件所在目录。
    """
    roots: set[str] = {str(real.parent)}
    for parent in real.parents:
        if parent.name.endswith(".framework"):
            roots.add(str(parent))
            break
    home = Path.home()
    try:
        rel = real.relative_to(home)
    except ValueError:
        rel = None
    if rel is not None and not _is_within(workspace, real) and not _is_within(data_dir, real):
        parts = rel.parts
        depth = 0
        for part in reversed(parts[:-1]):
            if part in {"bin", "sbin", "lib", "lib64", "libexec"}:
                depth += 1
                continue
            roots.add(str(home.joinpath(*parts[: len(parts) - 1 - depth])))
            break
        else:
            roots.add(str(real.parent))
    return roots


def _sbpl_path(path: str) -> str:
    """把路径转成 SBPL 字符串字面量（转义引号与反斜杠，避免注入 profile）。"""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sbpl_regex(pattern: str) -> str:
    """把正则字符串包成 SBPL 正则字面量 #"..."。

    与 _sbpl_path 一样只转义双引号；反斜杠是正则的一部分（如 \.），必须原样保留，
    否则会把字面点号误当成“任意字符”。
    """
    return '#"' + pattern.replace('"', '\\"') + '"'


def _sbpl_ci_fragment(fragment: str) -> str:
    """把 Python 正则片段转成 SBPL 写法并显式支持大小写不敏感。

    SBPL 正则不支持 (?i) 内联标志（实测会直接解析报错），因此把每个 ASCII 字母
    展开成 [xX] 字符类；\. 等转义序列和 .+ 等量词元字符原样保留。
    """
    out: list[str] = []
    i = 0
    while i < len(fragment):
        ch = fragment[i]
        if ch == "\\" and i + 1 < len(fragment):
            # 转义序列（\. \d 等）整体保留，字母也不展开。
            out.append(ch + fragment[i + 1])
            i += 2
            continue
        if ch.isalpha():
            out.append(f"[{ch.lower()}{ch.upper()}]")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _sbpl_sensitive_regexes(root: Path) -> tuple[str, str]:
    """构造命中 root 下敏感分量的两个 SBPL 正则（根级 + 嵌套级）。

    SBPL 正则不支持 (?:...) 非捕获分组，且实测 file-write-create 等写操作匹配
    不了带量词分组（(...)*）的模式，只有 .* 能跨目录命中完整目标路径；因此拆成：
    - 根级：^root/(名单)(/|$)
    - 嵌套：^root/.*/(名单)(/|$)
    .*/ 强制名单从完整分量起始处匹配，( /|$ ) 强制到分量结尾，等价于
    _is_sensitive_path 的 fullmatch 语义，不会误伤 mycredentials 之类仅包含
    敏感字样的普通文件；两条规则对读取、stat、list 与新建敏感文件都生效。
    """
    alternatives = "|".join(_sbpl_ci_fragment(frag) for frag in _SENSITIVE_PATH_SBPL_FRAGMENTS)
    escaped = re.escape(str(root))
    return (
        rf"^{escaped}/({alternatives})(/|$)",
        rf"^{escaped}/.*/({alternatives})(/|$)",
    )


def _path_ancestors(path: Path) -> list[str]:
    """返回路径的全部祖先目录字面量（不含路径本身）。

    macOS 的 realpath(3) 需要读取每个祖先分量的元数据；subpath 规则只放行目标
    及其子路径，不覆盖祖先。这里精确放行 Workspace Root / Shell data 的祖先链
    （例如 /、/Users、/Users/llh、/Users/llh/PycharmProjects），只暴露目录元数据，
    不放行祖先目录下的其它用户文件。
    """
    ancestors: list[str] = []
    acc = ""
    for part in Path(path).parts:
        if part == "/":
            acc = "/"
            ancestors.append(acc)
            continue
        acc = ("/" + part) if acc == "/" else acc.rstrip("/") + "/" + part
        ancestors.append(acc)
    # 去掉最后一项（路径本身由 subpath 规则放行），只保留祖先。
    return ancestors[:-1]


def _build_sandbox_profile(argv: list[str], workspace_root: Path, data_dir: Path) -> str:
    """构造 sandbox-exec SBPL profile：默认拒绝，白名单放行系统只读目录与工作区/私有 data。

    工作区/data 下的敏感路径由最后追加的显式 deny 覆盖（嵌套分量也命中），
    实测 macOS 沙箱对同一操作按“最后命中规则”生效，因此 deny 必须位于通用
    allow 之后才能优先于 Workspace Root / Shell data 的整体读写放行。
    """
    read_roots = set(_SYSTEM_READ_DIRS)
    real = _resolve_executable(str(argv[0]))
    if real is not None:
        read_roots.update(_derive_runtime_read_roots(real, workspace_root, data_dir))
    read_roots.update({str(workspace_root), str(data_dir)})
    metadata_literals = sorted(
        set(_path_ancestors(workspace_root)) | set(_path_ancestors(data_dir))
    )
    lines = [
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(allow process*)",
        "(allow network*)",
        "(allow file-read* "
        + " ".join(f"(subpath {_sbpl_path(path)})" for path in sorted(read_roots))
        + ")",
        "(allow file-read-metadata "
        + " ".join(f"(literal {_sbpl_path(path)})" for path in metadata_literals)
        + ")",
        "(allow file-write* "
        + f"(subpath {_sbpl_path(str(workspace_root))}) "
        + f"(subpath {_sbpl_path(str(data_dir))}))",
    ]
    # 敏感路径 deny 必须放在通用 allow 之后；(deny default) 只是兜底，
    # 不影响 deny/allow 之间的先后命中关系。
    for root in (workspace_root, data_dir):
        for pattern in _sbpl_sensitive_regexes(root):
            regex = _sbpl_regex(pattern)
            lines.append(f"(deny file-read* (regex {regex}))")
            lines.append(f"(deny file-write* (regex {regex}))")
    return "\n".join(lines)


def wrap_argv_with_isolation(
    argv: list[str],
    workspace_root: Path,
    data_dir: Path,
) -> tuple[list[str], str]:
    """把 argv 包装成受文件系统隔离保护的启动参数，返回 (wrapped_argv, isolation 标识)。

    当前 macOS + /usr/bin/sandbox-exec 使用沙箱 profile 包装；其它平台没有受支持的
    隔离后端时直接抛错，禁止无隔离执行。包装只做 argv 前置，不引入 shell。
    """
    runtime = shell_runtime_status()
    if not runtime["available"]:
        raise DailyAgentError(
            str(runtime["unavailable_reason"] or "文件系统隔离后端不可用，无法执行 Shell 命令。")
        )
    profile = _build_sandbox_profile(argv, workspace_root, data_dir)
    return [_SANDBOX_EXEC, "-p", profile, *argv], str(runtime["isolation"])
