"""项目结构检查 skill handler。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError
from daily_agent.skill_runtime import (
    SKILL_STATUS_ERROR,
    SKILL_STATUS_SUCCESS,
    SkillExecutionContext,
    SkillRunResult,
)

IGNORED_NAMES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".so", ".dylib", ".class"}
MAX_FILES = 180

IMPORTANT_PATHS = {
    "README.md": "项目说明",
    "pyproject.toml": "Python 项目配置",
    "src/daily_agent/api.py": "HTTP API 和静态前端入口",
    "src/daily_agent/web/index.html": "聊天工作台页面骨架",
    "src/daily_agent/web/app.js": "前端交互和流式聊天逻辑",
    "src/daily_agent/web/styles.css": "前端样式",
    "src/daily_agent/services/agent_service.py": "ask 主流程编排",
    "src/daily_agent/react_runner.py": "Agent 工具调用循环主链路",
    "src/daily_agent/skill_tools.py": "把技能适配成模型可调用的工具",
    "src/daily_agent/skill_gateway.py": "命令式技能入口分流",
    "src/daily_agent/skill_runtime.py": "技能执行运行时",
    "src/daily_agent/executor.py": "离线降级任务执行器",
    "src/daily_agent/planner.py": "规则规划器（降级/预览用）",
    "src/daily_agent/services/scheduler_service.py": "定时任务服务",
    "src/daily_agent/worker.py": "定时任务 worker",
    "src/daily_agent/services/report_service.py": "日报生成服务",
    "src/daily_agent/memory.py": "MySQL 记忆和任务存储",
}


@dataclass(frozen=True)
class ProjectSnapshot:
    root: Path
    top_dirs: list[str] = field(default_factory=list)
    top_files: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    important: dict[str, str] = field(default_factory=dict)
    extension_counts: dict[str, int] = field(default_factory=dict)


def run(*, context: SkillExecutionContext) -> SkillRunResult:
    """执行项目结构检查。"""
    inputs = dict(context.inputs)
    try:
        root = _resolve_root(inputs.get("path"), context.working_directory)
        snapshot = _inspect_project(root)
        response = _render_response(snapshot=snapshot, query=context.query)
        observation = {
            "root": str(snapshot.root),
            "top_dirs": snapshot.top_dirs,
            "top_files": snapshot.top_files,
            "important_paths": list(snapshot.important.keys()),
            "file_count_sampled": len(snapshot.files),
            "extension_counts": snapshot.extension_counts,
        }
        return SkillRunResult(
            status=SKILL_STATUS_SUCCESS,
            response_text=response,
            stop_reason="项目结构检查完成",
            inputs={"path": str(root)},
            output_text=response,
            observation=observation,
            clear_session=True,
        )
    except DailyAgentError as exc:
        return SkillRunResult(
            status=SKILL_STATUS_ERROR,
            response_text=str(exc),
            stop_reason="项目结构检查失败",
            inputs=inputs,
            clear_session=True,
        )


def _resolve_root(raw_path: Any, working_directory: str) -> Path:
    value = str(raw_path or "").strip()
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(working_directory) / path
    else:
        path = Path(working_directory)

    root = path.resolve()
    if not root.exists():
        raise DailyAgentError(f"项目路径不存在：{root}")
    if not root.is_dir():
        raise DailyAgentError(f"项目路径不是目录：{root}")
    return root


def _inspect_project(root: Path) -> ProjectSnapshot:
    top_dirs: list[str] = []
    top_files: list[str] = []
    for item in sorted(root.iterdir(), key=lambda value: value.name.lower()):
        if _should_ignore(item):
            continue
        if item.is_dir():
            top_dirs.append(item.name)
        elif item.is_file():
            top_files.append(item.name)

    files = _collect_files(root)
    important = {
        relative_path: label
        for relative_path, label in IMPORTANT_PATHS.items()
        if (root / relative_path).exists()
    }
    extension_counts = Counter(_extension_label(Path(item)) for item in files)
    return ProjectSnapshot(
        root=root,
        top_dirs=top_dirs[:16],
        top_files=top_files[:16],
        files=files,
        important=important,
        extension_counts=dict(extension_counts.most_common(8)),
    )


def _collect_files(root: Path) -> list[str]:
    result: list[str] = []
    pending = [root]
    while pending and len(result) < MAX_FILES:
        current = pending.pop(0)
        try:
            children = sorted(current.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower()))
        except OSError:
            continue
        for child in children:
            if _should_ignore(child):
                continue
            if child.is_dir():
                pending.append(child)
                continue
            if child.is_file():
                result.append(child.relative_to(root).as_posix())
                if len(result) >= MAX_FILES:
                    break
    return result


def _render_response(*, snapshot: ProjectSnapshot, query: str) -> str:
    sections = [
        f"### 当前项目结构：{snapshot.root.name}",
        "",
        f"- 根目录：`{snapshot.root}`",
        f"- 顶层目录：{_join_items(snapshot.top_dirs) or '未发现明显目录'}",
        f"- 顶层文件：{_join_items(snapshot.top_files) or '未发现明显文件'}",
        f"- 文件类型采样：{_format_counts(snapshot.extension_counts) or '无'}",
        "",
        "### 关键模块",
        *_render_important_paths(snapshot),
    ]

    if _wants_chat_flow(query):
        sections.extend(["", "### 聊天主流程", *_render_chat_flow(snapshot)])
    if _wants_scheduler_flow(query):
        sections.extend(["", "### 调度主流程", *_render_scheduler_flow(snapshot)])

    sections.extend(["", "### 建议优先看的文件", *_render_focus_files(snapshot)])
    return "\n".join(sections).strip()


def _render_important_paths(snapshot: ProjectSnapshot) -> list[str]:
    if not snapshot.important:
        return ["- 暂未匹配到预设关键文件，可以先从顶层目录和 README 看起。"]
    return [f"- `{path}`：{label}" for path, label in snapshot.important.items()]


def _render_chat_flow(snapshot: ProjectSnapshot) -> list[str]:
    items = [
        "- 前端从 `src/daily_agent/web/index.html` 加载聊天工作台，`app.js` 负责发送消息、接收流式事件和更新执行过程卡片。",
        "- HTTP 层在 `src/daily_agent/api.py` 暴露 `/ask` 和 `/ask/stream`；流式接口把 trace、最终回答和错误按事件推给前端。",
        "- 业务编排入口是 `AskService.run`，先抽取记忆，再通过 `SkillGateway` 判断是否命中命令式技能或未完成会话。",
        "- 命中命令式技能时走 `SkillSlotFiller`、`SkillRuntime`，最后进入 `src/daily_agent/skill_handlers/` 下的具体 handler。",
        "- 其余自然语言交给 `AgentLoopRunner`：`build_agent` 把工具交给模型，模型在工具调用循环里自主选工具、看结果、再作答。",
    ]
    return _only_existing_flow_items(snapshot, items)


def _render_scheduler_flow(snapshot: ProjectSnapshot) -> list[str]:
    items = [
        "- 创建和管理入口在 `src/daily_agent/api.py` 的 `/schedules` 系列接口，以及 `src/daily_agent/cli.py` 的 `schedule` 子命令。",
        "- 核心逻辑集中在 `SchedulerService`：创建任务、计算 next_run_at、查询任务、暂停恢复、立即执行和扫描到点任务。",
        "- 目前任务类型主要是 `daily_report`，执行时会调用 `DailyReportService.generate` 生成日报。",
        "- `src/daily_agent/worker.py` 是一次性 worker，适合被 cron 或 systemd timer 周期调用，内部执行 `run_due_jobs`。",
        "- 运行结果会写入 scheduled job run、任务历史和报告 trace，方便前端或 CLI 回看。",
    ]
    return _only_existing_flow_items(snapshot, items)


def _render_focus_files(snapshot: ProjectSnapshot) -> list[str]:
    preferred = [
        "src/daily_agent/api.py",
        "src/daily_agent/services/agent_service.py",
        "src/daily_agent/web/app.js",
        "src/daily_agent/services/scheduler_service.py",
        "src/daily_agent/worker.py",
        "tests/test_services.py",
        "tests/test_scheduler.py",
    ]
    existing = [path for path in preferred if path in snapshot.files or (snapshot.root / path).exists()]
    if not existing:
        existing = snapshot.files[:8]
    return [f"- `{path}`" for path in existing[:8]]


def _only_existing_flow_items(snapshot: ProjectSnapshot, items: list[str]) -> list[str]:
    existing_paths = set(snapshot.files) | set(snapshot.important.keys())
    result: list[str] = []
    for item in items:
        path_markers = [part for part in item.split("`") if "/" in part and "." in part]
        if not path_markers or any(path in existing_paths or (snapshot.root / path).exists() for path in path_markers):
            result.append(item)
    return result or items


def _wants_chat_flow(query: str) -> bool:
    return any(token in query for token in ["聊天", "对话", "ask", "问答", "前端", "UI", "ui"])


def _wants_scheduler_flow(query: str) -> bool:
    return any(token in query for token in ["调度", "定时", "schedule", "scheduler", "worker", "任务"])


def _should_ignore(path: Path) -> bool:
    if path.name in IGNORED_NAMES:
        return True
    if path.suffix in IGNORED_SUFFIXES:
        return True
    return False


def _extension_label(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix else "[无后缀]"


def _join_items(items: list[str]) -> str:
    return "、".join(f"`{item}`" for item in items)


def _format_counts(counts: dict[str, int]) -> str:
    return "、".join(f"`{key}` {value}" for key, value in counts.items())
