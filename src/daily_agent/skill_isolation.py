from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from daily_agent.paths import AppPaths

SAFE_INHERITED_ENV_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


@dataclass(frozen=True)
class SkillIsolationPaths:
    """一轮 Skill 执行可使用的私有目录，以及受控子进程环境。"""

    skill_name: str
    skill_directory: Path
    resource_directory: Path
    home_directory: Path
    data_directory: Path
    temporary_directory: Path
    dependency_directory: Path
    python_dependency_directory: Path
    node_dependency_directory: Path

    def subprocess_environment(
        self,
        *,
        preserve_user_home: bool = False,
        include_gateway_key: bool = False,
    ) -> dict[str, str]:
        """只继承运行命令必需的普通环境变量，避免把主模型和数据库密钥交给第三方进程。"""
        environment = {
            key: os.environ[key]
            for key in SAFE_INHERITED_ENV_KEYS
            if os.environ.get(key)
        }
        home = Path.home() if preserve_user_home else self.home_directory
        private_bin = self.dependency_directory / "bin"
        original_path = environment.get("PATH", os.defpath)
        environment.update(
            {
                "HOME": str(home),
                "TMPDIR": str(self.temporary_directory),
                "XDG_CACHE_HOME": str(self.data_directory / "cache"),
                "XDG_CONFIG_HOME": str(self.data_directory / "config"),
                "XDG_DATA_HOME": str(self.data_directory / "share"),
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": str(self.python_dependency_directory),
                "NODE_PATH": str(self.node_dependency_directory),
                "NPM_CONFIG_CACHE": str(self.data_directory / "npm-cache"),
                "PATH": os.pathsep.join([str(private_bin), str(self.node_dependency_directory / ".bin"), original_path]),
                "SB_AGENT_SKILL_NAME": self.skill_name,
                "SB_AGENT_SKILL_DIR": str(self.skill_directory),
                "SB_AGENT_SKILL_RESOURCE_DIR": str(self.resource_directory),
                "SB_AGENT_SKILL_DATA_DIR": str(self.data_directory),
                "SB_AGENT_SKILL_TEMP_DIR": str(self.temporary_directory),
                "SB_AGENT_SKILL_DEPENDENCY_DIR": str(self.dependency_directory),
            }
        )
        if include_gateway_key and os.environ.get("AI_GATEWAY_API_KEY"):
            environment["AI_GATEWAY_API_KEY"] = os.environ["AI_GATEWAY_API_KEY"]
        return environment

    def cleanup_temporary(self) -> None:
        shutil.rmtree(self.temporary_directory, ignore_errors=True)

    def wrap_command(
        self,
        command: list[str],
        *,
        project_directory: str | Path,
        preserve_user_home: bool = False,
    ) -> tuple[list[str], str]:
        """在当前操作系统支持时增加文件访问限制；浏览器任务因复用登录态只做进程隔离。"""
        if preserve_user_home:
            return command, "browser_profile"
        sandbox_binary = shutil.which("sandbox-exec") if platform.system() == "Darwin" else None
        if not sandbox_binary:
            return command, "private_process"

        executable = Path(command[0]).expanduser().resolve()
        executable_root = executable.parent.parent
        project_root = Path(project_directory).expanduser().resolve()
        skill_runtime_root = self.dependency_directory.parent
        home = Path.home().resolve()
        readable = _unique_paths(
            [executable_root, self.skill_directory, project_root, skill_runtime_root]
        )
        writable = _unique_paths([project_root, skill_runtime_root])
        profile_parts = [
            "(version 1)",
            "(allow default)",
            f'(deny file-read-data (subpath "{_sandbox_literal(home)}"))',
        ]
        profile_parts.extend(
            f'(allow file-read-data (subpath "{_sandbox_literal(path)}"))'
            for path in readable
        )
        profile_parts.append(f'(deny file-write* (subpath "{_sandbox_literal(home)}"))')
        profile_parts.extend(
            f'(allow file-write* (subpath "{_sandbox_literal(path)}"))'
            for path in writable
        )
        sensitive_files = [
            project_root / ".env",
            project_root / ".env.local",
            project_root / ".env.production",
            project_root / ".env.development",
            project_root / ".git" / "config",
        ]
        profile_parts.extend(
            f'(deny file-read-data (literal "{_sandbox_literal(path)}"))'
            for path in sensitive_files
        )
        profile_parts.extend(
            f'(deny file-write* (literal "{_sandbox_literal(path)}"))'
            for path in sensitive_files
        )
        return [sandbox_binary, "-p", " ".join(profile_parts), *command], "filesystem_sandbox"


def prepare_skill_isolation(
    *,
    skill_name: str,
    skill_directory: str | Path,
    user_id: str,
    run_id: str | None,
    runtime_root: str | Path | None = None,
) -> SkillIsolationPaths:
    """为 Skill 创建稳定私有目录，并为当前运行分配一次性临时目录。"""
    skill_root = skill_runtime_directory(skill_name, runtime_root=runtime_root)
    user_slug = _path_slug(user_id)
    dependency_directory = skill_root / "dependencies"
    home_directory = skill_root / "home" / user_slug
    data_directory = skill_root / "data" / user_slug
    run_slug = _path_slug(run_id or f"run-{uuid.uuid4().hex}")
    temporary_directory = skill_root / "tmp" / run_slug
    python_dependencies = dependency_directory / "python"
    node_dependencies = dependency_directory / "node_modules"
    skill_path = Path(skill_directory).expanduser().resolve()
    resources = skill_path / "resources"
    resource_directory = resources if resources.is_dir() else skill_path

    for directory in (
        home_directory,
        data_directory / "cache",
        data_directory / "config",
        data_directory / "share",
        data_directory / "npm-cache",
        temporary_directory,
        dependency_directory / "bin",
        python_dependencies,
        node_dependencies,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            # Windows 或受限文件系统可能不支持 POSIX 权限；目录隔离仍然继续生效。
            pass

    return SkillIsolationPaths(
        skill_name=skill_name,
        skill_directory=skill_path,
        resource_directory=resource_directory,
        home_directory=home_directory,
        data_directory=data_directory,
        temporary_directory=temporary_directory,
        dependency_directory=dependency_directory,
        python_dependency_directory=python_dependencies,
        node_dependency_directory=node_dependencies,
    )


def skill_runtime_directory(skill_name: str, *, runtime_root: str | Path | None = None) -> Path:
    root = Path(runtime_root or (AppPaths.discover().skills_home / "runtime")).expanduser().resolve()
    return root / _path_slug(skill_name)


def _path_slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-_").lower() or "default"
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
    return f"{normalized[:48]}-{digest}"


def _sandbox_literal(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique
