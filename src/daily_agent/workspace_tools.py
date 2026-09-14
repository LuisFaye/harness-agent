"""原生受控工作区 Tool：在 AppPaths.workspace_root 内提供受控的文件读写能力。

安全边界
--------
- 唯一根目录是 workspace_root；相对路径按根目录解析，绝对路径必须仍落在根目录内。
- 拒绝 ``..``、符号链接逃逸和敏感路径（.git/.ssh/.aws/.codex/.sb-agent/.env*、私钥、凭据）。
- 只处理文本；二进制、超大文件明确报错，绝不把乱码交给模型。
- 遍历和搜索跳过虚拟环境、依赖目录、构建产物和常见缓存目录。
- 写 Tool 必须确认，且同一运行确认后不重复确认同名 Tool（复用 database_tools 机制）。
"""

from __future__ import annotations

import codecs
import fnmatch
from functools import partial
import json
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Any, Iterator

from daily_agent.errors import DailyAgentError
from daily_agent.paths import AppPaths
from daily_agent.user_workspace import user_workspace_root

# 各 Tool 的条数与体积硬上限，防止把超大结果塞进模型上下文。
MAX_LIST_DEPTH = 5
MAX_LIST_LIMIT = 500
MAX_SEARCH_LIMIT = 200
MAX_READ_LINES = 1000
MAX_LINE_CHARS = 4000
MAX_OUTPUT_CHARS = 120_000
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_WRITE_BYTES = 2 * 1024 * 1024
MAX_REPLACEMENTS = 100

# 自动遍历/搜索时跳过的目录：虚拟环境、依赖、构建产物和常见缓存。
SKIP_DIR_NAMES = frozenset(
    {
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        ".pytest_cache",
        "__pycache__",
        ".coverage",
        "htmlcov",
        ".cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".git",
    }
)

# 敏感路径/文件名：相对路径任一分量命中即拒绝或跳过；匹配大小写不敏感，
# 覆盖 .ENV、SECRET.PEM 等变体，并拦截常见凭据文件。
_SENSITIVE_NAME_RE = re.compile(
    r"^(?:\.git|\.ssh|\.aws|\.codex|\.sb-agent|\.env|\.env\..+|id_rsa|id_ed25519|"
    r"credentials|credentials\..+|\.netrc|\.npmrc|\.pypirc|config\.toml|"
    r"secret|secret\..+|secrets|secrets\..+|.+\.pem|.+\.key|.+\.p12|.+\.pfx)$",
    re.IGNORECASE,
)

# 五个原生 Workspace Tool 的稳定定义，ToolRegistry 与 Agent 构建共用。
WORKSPACE_TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "workspace_list_files",
        "description": (
            "列出工作区目录下的文件和子目录，可按深度和条数限制；"
            "只读，无需确认。返回的路径都是相对工作区根目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "工作区相对路径或仍位于工作区内的绝对路径，默认工作区根目录",
                },
                "max_depth": {"type": "integer", "description": "递归深度，1 到 5，默认 2"},
                "limit": {"type": "integer", "description": "返回条数上限，1 到 500，默认 200"},
            },
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "workspace_search_text",
        "description": (
            "在工作区文本文件里搜索关键词，返回文件相对路径、1 起始行号和单行摘要；"
            "只读，无需确认。自动跳过二进制、超大文件和缓存目录。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要搜索的关键词，不允许为空"},
                "path": {
                    "type": "string",
                    "description": "搜索起始目录，默认工作区根目录",
                },
                "file_glob": {
                    "type": "string",
                    "description": "只匹配这些文件名或相对路径（通配符），留空表示全部",
                },
                "limit": {"type": "integer", "description": "返回条数上限，1 到 200，默认 100"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "workspace_read_file",
        "description": (
            "按行分段读取工作区文本文件，带 1 起始行号；只读，无需确认。"
            "超大文件、二进制文件会被明确拒绝。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径"},
                "start_line": {"type": "integer", "description": "起始行号，1 起始，默认 1"},
                "max_lines": {"type": "integer", "description": "最多读取行数，1 到 1000，默认 400"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "workspace_write_file",
        "description": (
            "在工作区创建或覆盖文本文件：create 只新建、overwrite 只覆盖已存在文件；"
            "会自动创建工作区内的父目录并以安全方式写入，执行前需要用户确认。"
            "用户要带走的文件请写到 outputs/ 下。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "文件完整文本内容"},
                "mode": {
                    "type": "string",
                    "enum": ["create", "overwrite"],
                    "description": "create 只新建；overwrite 只覆盖，默认 create",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "requires_confirmation": True,
    },
    {
        "name": "workspace_replace_text",
        "description": (
            "在工作区文本文件中精确替换旧文本，命中次数必须与 expected_replacements 完全一致"
            "才会写入；不一致时不修改文件。执行前需要用户确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要修改的文件路径"},
                "old_text": {"type": "string", "description": "要替换的旧文本，不允许为空"},
                "new_text": {"type": "string", "description": "替换成的新文本，可为空字符串"},
                "expected_replacements": {
                    "type": "integer",
                    "description": "期望命中次数，1 到 100，默认 1",
                },
            },
            "required": ["path", "old_text"],
            "additionalProperties": False,
        },
        "requires_confirmation": True,
    },
)


def build_workspace_tools(
    user_id: str = "default",
    confirmed_tool_names: set[str] | None = None,
) -> list[Any]:
    """构建 Agno 原生 Workspace Tool 列表。

    写 Tool 复用数据库写 Tool 的确认机制：首次构建时 requires_confirmation=True，
    用户确认后 react_runner 会把已确认的工具名传回来，同名 Tool 不再重复确认。
    ToolRegistry 在这里延迟导入，避免与 tool_registry 模块形成循环依赖。
    """
    from agno.tools.function import Function
    from daily_agent.tool_registry import ToolRegistry

    registry = ToolRegistry()
    confirmed = {str(item).replace("-", "_") for item in (confirmed_tool_names or set())}
    workspace_root = user_workspace_root(user_id)
    entrypoints = {
        "workspace_list_files": partial(_list_files, workspace_root=workspace_root),
        "workspace_search_text": partial(_search_text, workspace_root=workspace_root),
        "workspace_read_file": partial(_read_file, workspace_root=workspace_root),
        "workspace_write_file": partial(_write_file, workspace_root=workspace_root),
        "workspace_replace_text": partial(_replace_text, workspace_root=workspace_root),
    }
    tools: list[Any] = []
    for spec in WORKSPACE_TOOL_DEFINITIONS:
        name = str(spec["name"])
        tool_id = f"workspace:workspace-root:{name}"
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


def _workspace_root(workspace_root: Path | None = None) -> Path:
    root = (workspace_root or AppPaths.discover().workspace_root).resolve()
    if not root.is_dir():
        raise DailyAgentError("工作区根目录不存在或不是目录。")
    return root


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """把用户传参钳制到安全范围；非法值回落默认，避免模型乱传导致越界。"""
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _is_sensitive_path(relative: Path) -> bool:
    """相对路径任一分量命中敏感名单就拒绝，避免 .env、密钥目录等被读取。"""
    return any(_SENSITIVE_NAME_RE.fullmatch(part) for part in relative.parts)


def _is_within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _relative_path(root: Path, path: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise DailyAgentError("路径超出工作区范围。") from exc


def _resolve_safe_path(raw: str, *, workspace_root: Path | None = None) -> tuple[Path, Path, Path]:
    """把用户路径解析成工作区内真实路径。

    返回 (工作区根目录, 解析后的绝对路径, 相对根目录路径)。resolve() 会解开已有
    符号链接，因此链接指向工作区外时必然落在根目录之外而被拒绝；同时既检查用户
    原始路径、也检查解析后路径的敏感分量，防止经链接绕过敏感名单。
    """
    root = _workspace_root(workspace_root)
    text = str(raw or "").strip()
    if not text:
        raise DailyAgentError("路径不能为空。")
    candidate = Path(text)
    if ".." in candidate.parts:
        raise DailyAgentError("路径不能包含 ..，防止越出工作区。")
    absolute = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = absolute.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise DailyAgentError(f"路径无法解析：{exc}") from exc
    if not _is_within(root, resolved):
        raise DailyAgentError("路径超出工作区范围。")
    relative = _relative_path(root, resolved)
    if candidate.is_absolute():
        try:
            raw_relative = candidate.relative_to(root)
        except ValueError:
            raw_relative = None
    else:
        raw_relative = candidate
    if (raw_relative is not None and _is_sensitive_path(raw_relative)) or _is_sensitive_path(relative):
        raise DailyAgentError("路径涉及敏感文件或目录（.git/.env/密钥/凭据等），已拒绝访问。")
    return root, resolved, relative


def _detect_text_encoding(data: bytes, *, path: str) -> str:
    """判定文本编码类别，返回 utf-8-sig / utf-8 / gbk。

    顺序与读取接口一致：先识别 UTF-8 BOM，再试 UTF-8，最后试 GBK。
    识别结果供 replace 按原编码写回，避免把 GBK 文件重编码成 UTF-8
    或丢失 UTF-8 BOM。
    """
    if b"\x00" in data[:65536]:
        raise DailyAgentError(f"文件 {path} 是二进制文件，只支持文本文件。")
    if data.startswith(codecs.BOM_UTF8):
        try:
            data.decode("utf-8-sig")
            return "utf-8-sig"
        except UnicodeDecodeError:
            # 以 BOM 开头但整体不是合法 UTF-8，按原有顺序继续尝试其他编码。
            pass
    for encoding in ("utf-8", "gbk"):
        try:
            data.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise DailyAgentError(f"文件 {path} 无法安全解码为文本（不是 UTF-8/GBK）。")


def _decode_text(data: bytes, *, path: str) -> str:
    """安全解码文本；读取接口对模型输出始终返回 Unicode 文本。"""
    encoding = _detect_text_encoding(data, path=path)
    return data.decode(encoding)


def _truncate_line(line: str, max_chars: int = MAX_LINE_CHARS) -> str:
    if len(line) <= max_chars:
        return line
    return line[: max_chars - 1] + "…"


def _write_temp_file(directory: Path, data: bytes, mode: int | None) -> str:
    """在同目录创建临时文件并写完整内容，返回临时文件路径。

    mode 为 None 时按 create 语义：os.open 传入 0666，由内核应用进程
    umask，得到 0666 & ~umask；否则写盘前把临时文件 chmod 成
    mode & 0o777（只保留常规权限位，不复制 setuid/setgid/sticky 特殊位）。
    Windows 或不支持 chmod 的文件系统上 chmod 失败时降级，不阻断写入。
    """
    tmp_name = ""
    fd = -1
    for _ in range(100):
        candidate = str(directory / f".sb-agent-ws-{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            tmp_name = candidate
            break
        except FileExistsError:
            continue
    if not tmp_name or fd < 0:
        raise DailyAgentError("写入文件失败：无法在同目录创建临时文件。")
    try:
        if mode is not None:
            try:
                os.chmod(tmp_name, mode & 0o777)
            except OSError:
                pass
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise DailyAgentError(f"写入文件失败：{exc}") from exc
    return tmp_name


def _atomic_replace(target: Path, tmp_name: str) -> None:
    """同目录原子替换目标文件（overwrite/replace 共用）；失败时清理临时文件。"""
    try:
        os.replace(tmp_name, target)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise DailyAgentError(f"写入文件失败：{exc}") from exc


def _atomic_create(target: Path, tmp_name: str) -> None:
    """原子发布 create：hard link 只在目标不存在时成功，绝不覆盖。

    目标在预检后、发布前被其他进程创建时，os.link 抛 FileExistsError，
    返回清楚的错误并清理临时文件；文件系统不支持硬链接时安全失败，
    绝不退化成可能覆盖已存在目标的 os.replace。
    """
    try:
        os.link(tmp_name, target)
    except FileExistsError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise DailyAgentError("目标文件已被其他进程创建，create 模式未做任何修改。") from exc
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise DailyAgentError(
            f"无法以“不存在才创建”方式发布文件（当前文件系统可能不支持硬链接）：{exc}"
        ) from exc
    try:
        os.unlink(tmp_name)
    except OSError:
        pass


def _list_files(
    path: str = ".",
    max_depth: int = 2,
    limit: int = 200,
    *,
    workspace_root: Path | None = None,
) -> str:
    """按层级稳定列出工作区条目；深度和条数都做硬上限，超限标记 truncated。"""
    root, target, relative = _resolve_safe_path(path, workspace_root=workspace_root)
    if not target.is_dir():
        raise DailyAgentError("列目录的目标必须是一个目录。")
    depth = _clamp_int(max_depth, 2, 1, MAX_LIST_DEPTH)
    cap = _clamp_int(limit, 200, 1, MAX_LIST_LIMIT)
    items: list[dict[str, Any]] = []
    truncated = False
    pending: list[tuple[Path, int]] = [(target, 0)]
    while pending and not truncated:
        dir_path, level = pending.pop(0)
        if level >= depth:
            continue
        try:
            children = sorted(dir_path.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for child in children:
            # 符号链接既不跟随也不展示，避免泄露工作区外目标。
            if child.is_symlink():
                continue
            child_relative = _relative_path(root, child)
            if _is_sensitive_path(child_relative):
                continue
            try:
                if child.is_dir():
                    if child.name in SKIP_DIR_NAMES:
                        continue
                    entry: dict[str, Any] = {
                        "path": child_relative.as_posix(),
                        "type": "dir",
                        "size": 0,
                    }
                    if level + 1 < depth:
                        pending.append((child, level + 1))
                elif child.is_file():
                    entry = {
                        "path": child_relative.as_posix(),
                        "type": "file",
                        "size": child.stat().st_size,
                    }
                else:
                    continue
            except OSError:
                continue
            items.append(entry)
            if len(items) >= cap:
                truncated = True
                break
    return json.dumps(
        {
            "path": relative.as_posix(),
            "items": items,
            "total": len(items),
            "truncated": truncated,
        },
        ensure_ascii=False,
    )


def _iter_text_files(start: Path, root: Path) -> Iterator[tuple[Path, str]]:
    """深度优先遍历文本候选文件：跳过符号链接、敏感路径、缓存目录和超大文件。"""
    pending: list[Path] = [start]
    while pending:
        dir_path = pending.pop(0)
        try:
            children = sorted(dir_path.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for child in children:
            if child.is_symlink():
                continue
            child_relative = _relative_path(root, child)
            if _is_sensitive_path(child_relative):
                continue
            if child.is_dir():
                if child.name in SKIP_DIR_NAMES:
                    continue
                pending.append(child)
            elif child.is_file():
                try:
                    if child.stat().st_size > MAX_SCAN_BYTES:
                        continue
                except OSError:
                    continue
                yield child, child_relative.as_posix()


def _search_text(
    query: str = "",
    path: str = ".",
    file_glob: str = "",
    limit: int = 100,
    *,
    workspace_root: Path | None = None,
) -> str:
    """跨文本文件搜索关键词；返回相对路径、1 起始行号和截断后的单行摘要。"""
    keyword = str(query or "").strip()
    if not keyword:
        raise DailyAgentError("搜索关键词不能为空。")
    root, target, relative = _resolve_safe_path(path, workspace_root=workspace_root)
    if not target.is_dir():
        raise DailyAgentError("搜索目标必须是一个目录。")
    cap = _clamp_int(limit, 100, 1, MAX_SEARCH_LIMIT)
    glob_pattern = str(file_glob or "").strip()
    results: list[dict[str, Any]] = []
    truncated = False
    for file_path, file_relative in _iter_text_files(target, root):
        # file_glob 只做文件名/相对路径过滤，不影响根目录边界。
        if glob_pattern and not (
            fnmatch.fnmatch(file_relative, glob_pattern)
            or fnmatch.fnmatch(Path(file_relative).name, glob_pattern)
        ):
            continue
        try:
            data = file_path.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_SCAN_BYTES or b"\x00" in data[:65536]:
            continue
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("gbk")
            except UnicodeDecodeError:
                continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if keyword in line:
                results.append(
                    {
                        "path": file_relative,
                        "line": line_no,
                        "snippet": _truncate_line(line.strip(), 300),
                    }
                )
                if len(results) >= cap:
                    truncated = True
                    break
        if truncated:
            break
    return json.dumps(
        {
            "path": relative.as_posix(),
            "items": results,
            "total": len(results),
            "truncated": truncated,
        },
        ensure_ascii=False,
    )


def _read_file(
    path: str = "",
    start_line: int = 1,
    max_lines: int = 400,
    *,
    workspace_root: Path | None = None,
) -> str:
    """按行读取文本文件；单文件大小、单行长度、行数和总输出字符都有硬上限。"""
    root, target, relative = _resolve_safe_path(path, workspace_root=workspace_root)
    if not target.is_file():
        raise DailyAgentError("读取目标必须是普通文件。")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise DailyAgentError(f"无法读取文件：{exc}") from exc
    if size > MAX_READ_BYTES:
        raise DailyAgentError(f"文件过大（{size} 字节），超过单次读取上限 {MAX_READ_BYTES} 字节。")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise DailyAgentError(f"无法读取文件：{exc}") from exc
    text = _decode_text(data, path=relative.as_posix())
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return json.dumps(
            {
                "path": relative.as_posix(),
                "total_lines": 0,
                "start_line": 1,
                "end_line": 0,
                "content": [],
                "truncated": False,
            },
            ensure_ascii=False,
        )
    start = max(1, min(_clamp_int(start_line, 1, 1, total), total))
    count = _clamp_int(max_lines, 400, 1, MAX_READ_LINES)
    end = min(total, start + count - 1)
    content: list[dict[str, Any]] = []
    used_chars = 0
    output_truncated = False
    for line_no in range(start, end + 1):
        raw_line = lines[line_no - 1]
        shown = _truncate_line(raw_line)
        used_chars += len(shown) + len(str(line_no)) + 4
        if used_chars > MAX_OUTPUT_CHARS:
            output_truncated = True
            break
        content.append({"line": line_no, "text": shown})
    line_truncated = any(len(lines[index]) > MAX_LINE_CHARS for index in range(start - 1, end))
    truncated = output_truncated or end < total or line_truncated
    return json.dumps(
        {
            "path": relative.as_posix(),
            "total_lines": total,
            "start_line": start,
            "end_line": end,
            "content": content,
            "truncated": truncated,
        },
        ensure_ascii=False,
    )


def _write_file(
    path: str = "",
    content: str = "",
    mode: str = "create",
    *,
    workspace_root: Path | None = None,
) -> str:
    """创建或覆盖工作区文本文件；create/overwrite 模式互相保护，写入原子化。

    create 用原子 hard link 发布，保证并发下也不会覆盖已存在目标，权限遵循
    当前 umask（0666 & ~umask）；overwrite 原子替换并保留原文件常规权限位。
    workspace_write_file 的新建与完整覆盖始终写 UTF-8，符合完整内容写入契约。
    """
    root, target, relative = _resolve_safe_path(path, workspace_root=workspace_root)
    write_mode = str(mode or "create").strip().lower()
    if write_mode not in {"create", "overwrite"}:
        raise DailyAgentError("mode 只允许 create 或 overwrite。")
    if not isinstance(content, str):
        raise DailyAgentError("content 必须是文本字符串。")
    data = content.encode("utf-8")
    if b"\x00" in data:
        raise DailyAgentError("写入内容包含二进制数据，只允许纯文本。")
    if len(data) > MAX_WRITE_BYTES:
        raise DailyAgentError(f"写入内容过大（{len(data)} 字节），超过上限 {MAX_WRITE_BYTES} 字节。")
    if write_mode == "create":
        # 预检只用于给出友好错误；最终仍以原子 hard link 保证不覆盖并发创建的目标。
        if target.exists():
            raise DailyAgentError("目标文件已存在，create 模式不会覆盖；如需覆盖请改用 overwrite 模式。")
    else:
        if not target.is_file():
            if not target.exists():
                raise DailyAgentError("目标文件不存在，overwrite 模式不能凭空创建；如需新建请改用 create 模式。")
            raise DailyAgentError("目标已存在但不是普通文件，拒绝覆盖。")
    # 先创建父目录，再写同目录临时文件，保证 replace 原子且不留下半写入。
    target.parent.mkdir(parents=True, exist_ok=True)
    if write_mode == "create":
        tmp_name = _write_temp_file(target.parent, data, mode=None)
        _atomic_create(target, tmp_name)
    else:
        try:
            existing_mode = stat.S_IMODE(target.stat().st_mode) & 0o777
        except OSError as exc:
            raise DailyAgentError(f"无法读取目标文件信息：{exc}") from exc
        tmp_name = _write_temp_file(target.parent, data, mode=existing_mode)
        _atomic_replace(target, tmp_name)
    return json.dumps(
        {"path": relative.as_posix(), "bytes": len(data), "mode": write_mode},
        ensure_ascii=False,
    )


def _replace_text(
    path: str = "",
    old_text: str = "",
    new_text: str = "",
    expected_replacements: int = 1,
    *,
    workspace_root: Path | None = None,
) -> str:
    """精确替换文本；命中次数与预期完全一致才写入，否则文件保持不变。

    按原文件编码类别写回（UTF-8 BOM 保留 BOM、UTF-8 仍为 UTF-8、GBK 仍按 GBK），
    新文本无法用原编码表达时安全失败且不修改文件；同时保留原文件常规权限位。
    """
    root, target, relative = _resolve_safe_path(path, workspace_root=workspace_root)
    old = str(old_text or "")
    if not old:
        raise DailyAgentError("old_text 不能为空。")
    expected = _clamp_int(expected_replacements, 1, 1, MAX_REPLACEMENTS)
    if not target.is_file():
        raise DailyAgentError("替换目标必须是普通文件。")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise DailyAgentError(f"无法读取文件：{exc}") from exc
    if size > MAX_READ_BYTES:
        raise DailyAgentError(f"文件过大（{size} 字节），超过可处理上限 {MAX_READ_BYTES} 字节。")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise DailyAgentError(f"无法读取文件：{exc}") from exc
    encoding = _detect_text_encoding(data, path=relative.as_posix())
    text = data.decode(encoding)
    hit_count = text.count(old)
    if hit_count != expected:
        raise DailyAgentError(
            f"替换命中次数与预期不符：预期 {expected} 次，实际 {hit_count} 次，未做任何修改。"
        )
    result = text.replace(old, str(new_text or ""))
    try:
        result_data = result.encode(encoding)
    except UnicodeEncodeError as exc:
        raise DailyAgentError(
            f"替换后的文本无法用原文件编码（{encoding}）表达，已放弃写入，文件保持不变。"
        ) from exc
    if len(result_data) > MAX_WRITE_BYTES:
        raise DailyAgentError(
            f"替换后文件过大（{len(result_data)} 字节），超过上限 {MAX_WRITE_BYTES} 字节，未写入。"
        )
    existing_mode = stat.S_IMODE(target.stat().st_mode) & 0o777
    tmp_name = _write_temp_file(target.parent, result_data, mode=existing_mode)
    _atomic_replace(target, tmp_name)
    return json.dumps(
        {"path": relative.as_posix(), "replacements": hit_count},
        ensure_ascii=False,
    )
