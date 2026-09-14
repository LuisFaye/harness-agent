from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError
from daily_agent.paths import AppPaths
from daily_agent.capability_security import review_skill_directory, security_block_message
from daily_agent.skill_compat import analyze_skill_compatibility
from daily_agent.skill_isolation import skill_runtime_directory

BUILTIN_SKILLS_HOME = Path(__file__).resolve().parent / "skills"
VALID_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
GITHUB_URL_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:)(?P<owner>[^/\s:]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$")
SKILLS_SH_URL_RE = re.compile(
    r"^https://(?:www\.)?skills\.sh/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/(?P<skill>[^/?#\s]+)/?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SkillRecord:
    """工作台和运行时共用的一条 skill 元数据。"""

    name: str
    description: str
    status: str
    source: str
    path: str
    tools: tuple[str, ...]
    can_remove: bool = False
    can_adopt: bool = False
    origin_path: str | None = None
    installed_at: str | None = None
    error_message: str | None = None
    tool_name: str | None = None
    triggers: tuple[str, ...] = ()
    intent_examples: tuple[str, ...] = ()
    input_schema: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    interaction_mode: str = "react_tool"
    requires_confirmation: bool = False
    compatibility: dict[str, Any] | None = None
    capabilities: tuple[str, ...] = ()
    risk_level: str = "low"
    install_source: str | None = None
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "source": self.source,
            "path": self.path,
            "tools": list(self.tools),
            "can_remove": self.can_remove,
            "can_adopt": self.can_adopt,
            "origin_path": self.origin_path,
            "installed_at": self.installed_at,
            "error_message": self.error_message,
            "tool_name": self.tool_name,
            "triggers": list(self.triggers),
            "intent_examples": list(self.intent_examples),
            "input_schema": self.input_schema,
            "execution": self.execution,
            "interaction_mode": self.interaction_mode,
            "requires_confirmation": self.requires_confirmation,
            "compatibility": self.compatibility,
            "capabilities": list(self.capabilities),
            "risk_level": self.risk_level,
            "install_source": self.install_source,
            "diagnostics": list(self.diagnostics),
        }


class SkillCatalog:
    """管理内置 skill、本地导入 skill 以及启停状态。

    设计目标：
    1. 不依赖 MySQL，避免首次使用时还要先初始化数据库；
    2. 内置 skill 和导入 skill 统一走一份注册表；
    3. 启停状态改完立即生效，供 API / 前端 / 运行时统一读取。
    """

    def __init__(
        self,
        *,
        builtin_home: Path | None = None,
        imported_home: Path | None = None,
        registry_path: Path | None = None,
        paths: AppPaths | None = None,
    ) -> None:
        resolved_paths = paths or AppPaths.discover()
        self.builtin_home = (builtin_home or BUILTIN_SKILLS_HOME).resolve()
        self.imported_home = (imported_home or resolved_paths.imported_skills_home).resolve()
        self.registry_path = (registry_path or resolved_paths.skill_registry_path).resolve()
        self.sources_home = self.imported_home.parent / "sources"

    def list_skills(self) -> list[SkillRecord]:
        registry = self._load_registry()
        items = self._load_workspace_records(registry) + self._load_imported_records(registry)
        source_order = {"workspace": 0, "imported": 1}
        return sorted(items, key=lambda item: (source_order.get(item.source, 9), item.name))

    def import_source(self, source: str, enable: bool = True) -> list[SkillRecord]:
        normalized = source.strip()
        if not normalized:
            raise DailyAgentError("导入来源不能为空。")
        if _is_skills_sh_url(normalized):
            return self.import_skills_sh_url(normalized, enable=enable)
        if _is_github_url(normalized):
            return self.import_github_url(normalized, enable=enable)
        return self.import_local_path(normalized, enable=enable)

    def import_local_path(self, raw_path: str, enable: bool = True) -> list[SkillRecord]:
        return self._import_local_path(raw_path, enable=enable, install_source=raw_path)

    def import_github_url(self, source_url: str, enable: bool = True) -> list[SkillRecord]:
        return self._import_github_url(source_url, enable=enable)

    def import_skills_sh_url(self, source_url: str, enable: bool = True) -> list[SkillRecord]:
        match = SKILLS_SH_URL_RE.match(source_url.strip())
        if not match:
            raise DailyAgentError("skills.sh 地址格式不正确，应为 https://skills.sh/owner/repo/skill。")
        github_url = f"https://github.com/{match.group('owner')}/{match.group('repo')}"
        return self._import_github_url(
            github_url,
            enable=enable,
            selected_skill_name=match.group("skill"),
            install_source=source_url.strip(),
        )

    def _import_github_url(
        self,
        source_url: str,
        *,
        enable: bool,
        selected_skill_name: str | None = None,
        install_source: str | None = None,
    ) -> list[SkillRecord]:
        normalized_url = _normalize_github_url(source_url)
        effective_install_source = install_source or normalized_url
        repo_name = _github_repo_name(normalized_url)
        existing_records = self._records_for_install_source(effective_install_source)
        if existing_records:
            return existing_records

        source_folder_name = f"{repo_name}-{selected_skill_name}" if selected_skill_name else repo_name
        target_dir = self.sources_home / source_folder_name
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

        self.sources_home.mkdir(parents=True, exist_ok=True)
        git_binary = shutil.which("git")
        if not git_binary:
            raise DailyAgentError("本机没有找到 git，无法从 GitHub 导入 skill。")

        try:
            subprocess.run(
                [git_binary, "clone", "--depth", "1", normalized_url, str(target_dir)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise DailyAgentError("从 GitHub clone skill 仓库超时。") from exc
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            detail = (exc.stderr or exc.stdout or "").strip()
            raise DailyAgentError(f"从 GitHub clone skill 仓库失败：{detail or exc}") from exc

        try:
            return self._import_local_path(
                target_dir,
                enable=enable,
                install_source=effective_install_source,
                selected_skill_name=selected_skill_name,
            )
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

    def _import_local_path(
        self,
        raw_path: str | Path,
        enable: bool = True,
        install_source: str | None = None,
        selected_skill_name: str | None = None,
    ) -> list[SkillRecord]:
        source_path = Path(raw_path).expanduser().resolve()
        if not source_path.exists():
            raise DailyAgentError(f"待导入的路径不存在：{source_path}")
        if not source_path.is_dir():
            raise DailyAgentError("只支持导入本地 skill 目录。")

        skill_dirs = self._discover_skill_dirs(source_path)
        if selected_skill_name:
            skill_dirs = [
                folder
                for folder in skill_dirs
                if self._read_skill_metadata(
                    folder,
                    source="imported",
                    install_source=install_source or str(source_path),
                )["name"] == selected_skill_name
            ]
        if not skill_dirs:
            if selected_skill_name:
                raise DailyAgentError(f"仓库里没有找到名为 {selected_skill_name} 的 skill。")
            raise DailyAgentError("没有找到可导入的 skill，目录里至少要有一个包含 SKILL.md 的子目录。")

        existing_names = {item.name for item in self.list_skills()}
        registry = self._load_registry()
        imported_records: list[SkillRecord] = []
        pending_entries: dict[str, dict[str, Any]] = {}
        is_github_source = bool(install_source and _is_github_url(install_source))
        target_names_by_folder: dict[Path, str] = {}

        # 先完整校验一遍，确保批量导入时要么全成功，要么一个都不落盘。
        for folder in skill_dirs:
            security_review = review_skill_directory(folder)
            if security_review.blocked:
                raise DailyAgentError(security_block_message(security_review, subject=f"Skill {folder.name} "))
            metadata = self._read_skill_metadata(folder, source="imported", install_source=install_source or str(source_path))
            raw_skill_name = metadata["name"]
            skill_name = self._resolve_import_skill_name(
                raw_name=raw_skill_name,
                source_folder=folder,
                existing_names=existing_names,
                pending_entries=pending_entries,
                is_github_source=is_github_source,
                install_source=install_source or str(source_path),
            )
            target_dir = self.imported_home / skill_name
            if target_dir.exists():
                raise DailyAgentError(f"目标目录已存在，请先删除旧 skill 后再导入：{target_dir}")
            target_names_by_folder[folder] = skill_name
            pending_entries[skill_name] = {
                "status": "enabled" if enable else "disabled",
                "source": "imported",
                "path": str(target_dir),
                "origin_path": str(folder),
                "install_source": install_source or str(source_path),
                "installed_at": _utc_now_iso(),
            }
            imported_records.append(
                self._record_from_metadata(
                    metadata=metadata,
                    status="enabled" if enable else "disabled",
                    source="imported",
                    path=str(target_dir),
                    can_remove=True,
                    origin_path=str(folder),
                    installed_at=pending_entries[skill_name]["installed_at"],
                    install_source=install_source or str(source_path),
                    name_override=skill_name,
                )
            )

        self.imported_home.mkdir(parents=True, exist_ok=True)
        copied_targets: list[Path] = []
        try:
            for folder in skill_dirs:
                target_dir = self.imported_home / target_names_by_folder[folder]
                shutil.copytree(folder, target_dir)
                copied_targets.append(target_dir)
            registry.setdefault("skills", {}).update(pending_entries)
            self._save_registry(registry)
        except Exception as exc:
            # 导入失败时尽量回滚，避免工作台里出现“登记了但目录不完整”的脏状态。
            for target_dir in copied_targets:
                shutil.rmtree(target_dir, ignore_errors=True)
            raise DailyAgentError(f"导入本地 skill 失败：{exc}") from exc

        return imported_records

    def adopt_workspace_skill(self, name: str) -> SkillRecord:
        normalized_name = name.strip()
        records = {item.name: item for item in self.list_skills()}
        current = records.get(normalized_name)
        if not current:
            raise DailyAgentError(f"没有找到 skill：{normalized_name}")
        if current.source != "workspace":
            raise DailyAgentError("只有 workspace 类型的 skill 才需要迁移接管。")

        source_dir = Path(current.path).expanduser()
        if not source_dir.exists():
            raise DailyAgentError(f"待迁移的 workspace skill 目录不存在：{source_dir}")

        registry = self._load_registry()
        target_dir = self.imported_home / normalized_name
        if target_dir.exists():
            raise DailyAgentError(f"目标导入目录已存在，请先处理：{target_dir}")

        self.imported_home.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, target_dir)
        skills_section = registry.setdefault("skills", {})
        previous = dict(skills_section.get(normalized_name, {}))
        skills_section[normalized_name] = {
            "status": str(previous.get("status", current.status or "enabled")),
            "source": "imported",
            "path": str(target_dir),
            "origin_path": str(source_dir),
            "install_source": current.install_source or str(source_dir),
            "installed_at": str(previous.get("installed_at") or _utc_now_iso()),
        }
        self._save_registry(registry)

        metadata = self._read_skill_metadata(
            target_dir,
            source="imported",
            install_source=current.install_source or str(source_dir),
        )
        return self._record_from_metadata(
            metadata=metadata,
            status=str(skills_section[normalized_name]["status"]),
            source="imported",
            path=str(target_dir),
            can_remove=True,
            origin_path=str(source_dir),
            installed_at=str(skills_section[normalized_name]["installed_at"]),
            install_source=current.install_source or str(source_dir),
            name_override=normalized_name,
        )

    def set_skill_status(self, name: str, status: str) -> SkillRecord:
        normalized_name = name.strip()
        normalized_status = status.strip().lower()
        if normalized_status not in {"enabled", "disabled"}:
            raise DailyAgentError("skill 状态只支持 enabled 或 disabled。")

        records = {item.name: item for item in self.list_skills()}
        current = records.get(normalized_name)
        if not current:
            raise DailyAgentError(f"没有找到 skill：{normalized_name}")

        registry = self._load_registry()
        skills_section = registry.setdefault("skills", {})
        entry = dict(skills_section.get(normalized_name, {}))
        entry["status"] = normalized_status
        if current.source == "imported":
            entry.setdefault("source", "imported")
            entry.setdefault("path", current.path)
            if current.origin_path:
                entry.setdefault("origin_path", current.origin_path)
            if current.installed_at:
                entry.setdefault("installed_at", current.installed_at)
            if current.install_source:
                entry.setdefault("install_source", current.install_source)
        skills_section[normalized_name] = entry
        self._save_registry(registry)

        return SkillRecord(
            name=current.name,
            description=current.description,
            status=normalized_status,
            source=current.source,
            path=current.path,
            tools=current.tools,
            can_remove=current.can_remove,
            can_adopt=current.can_adopt,
            origin_path=current.origin_path,
            installed_at=current.installed_at,
            error_message=current.error_message,
            tool_name=current.tool_name,
            triggers=current.triggers,
            intent_examples=current.intent_examples,
            input_schema=current.input_schema,
            execution=current.execution,
            interaction_mode=current.interaction_mode,
            requires_confirmation=current.requires_confirmation,
            compatibility=current.compatibility,
            capabilities=current.capabilities,
            risk_level=current.risk_level,
            install_source=current.install_source,
            diagnostics=current.diagnostics,
        )

    def remove_skill(self, name: str) -> bool:
        normalized_name = name.strip()
        registry = self._load_registry()
        registry_name = self._resolve_registry_name_for_remove(normalized_name, registry)
        entry = dict(registry.get("skills", {}).get(registry_name, {}))
        if entry.get("source") != "imported":
            raise DailyAgentError("只允许删除导入的本地 skill，内置 skill 不能删除。")

        target_dir = Path(str(entry.get("path", ""))).expanduser()
        registry["skills"].pop(registry_name, None)
        self._save_registry(registry)
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        runtime_dir = skill_runtime_directory(
            registry_name,
            runtime_root=self.imported_home.parent / "runtime",
        )
        shutil.rmtree(runtime_dir, ignore_errors=True)
        return True

    def _resolve_registry_name_for_remove(self, requested_name: str, registry: dict[str, Any]) -> str:
        if requested_name in registry.get("skills", {}):
            return requested_name
        for name, entry in registry.get("skills", {}).items():
            if entry.get("source") != "imported":
                continue
            skill_dir = Path(str(entry.get("path", ""))).expanduser()
            try:
                metadata = self._read_skill_metadata(
                    skill_dir,
                    source="imported",
                    install_source=str(entry.get("install_source") or entry.get("origin_path") or ""),
                )
            except DailyAgentError:
                continue
            if metadata.get("name") == requested_name:
                return name
        return requested_name

    def enabled_skill_paths(self) -> list[Path]:
        paths: list[Path] = []
        for item in self.list_skills():
            if item.status != "enabled":
                continue
            if item.error_message:
                continue
            skill_dir = Path(item.path)
            if (skill_dir / "SKILL.md").exists():
                paths.append(skill_dir)
        return paths

    def enabled_tool_names(self) -> set[str]:
        enabled_tools: set[str] = set()
        for item in self.list_skills():
            if item.status == "enabled" and not item.error_message:
                enabled_tools.update(item.tools)
        return enabled_tools

    def get_skill_name_for_tool(self, tool_name: str) -> str | None:
        normalized_tool_name = tool_name.strip()
        if not normalized_tool_name:
            return None
        for item in self.list_skills():
            if item.error_message:
                continue
            if normalized_tool_name == item.tool_name or normalized_tool_name == item.name or normalized_tool_name in item.tools:
                return item.name
        return None

    def enabled_skill_records(self) -> list[SkillRecord]:
        """返回当前启用且元数据完整的 skill 记录。"""
        return [item for item in self.list_skills() if item.status == "enabled" and not item.error_message]

    def get_skill(self, name: str) -> SkillRecord | None:
        normalized_name = name.strip()
        for item in self.list_skills():
            if item.name == normalized_name:
                return item
        return None

    def _source_url_registered(self, source_url: str) -> bool:
        return bool(self._records_for_install_source(source_url))

    def _records_for_install_source(self, source_url: str) -> list[SkillRecord]:
        normalized = _normalize_install_source(source_url)
        records: list[SkillRecord] = []
        for item in self.list_skills():
            if item.install_source and _normalize_install_source(item.install_source) == normalized:
                records.append(item)
        return records

    def _resolve_import_skill_name(
        self,
        *,
        raw_name: str,
        source_folder: Path,
        existing_names: set[str],
        pending_entries: dict[str, dict[str, Any]],
        is_github_source: bool,
        install_source: str,
    ) -> str:
        if raw_name not in existing_names and raw_name not in pending_entries:
            return raw_name
        if not is_github_source:
            raise DailyAgentError(f"skill 名称已存在，不能重复导入：{raw_name}")

        prefix = _github_repo_name(install_source)
        candidates = [
            _normalize_skill_name(prefix),
            _normalize_skill_name(f"{prefix}-{source_folder.name}"),
            _normalize_skill_name(f"{prefix}-{raw_name}"),
        ]
        for index in range(2, 100):
            candidates.append(_normalize_skill_name(f"{prefix}-{raw_name}-{index}"))

        for candidate in candidates:
            if not candidate:
                continue
            if candidate in existing_names or candidate in pending_entries:
                continue
            if (self.imported_home / candidate).exists():
                continue
            return candidate
        raise DailyAgentError(f"skill 名称已存在，且自动改名失败：{raw_name}")

    def _record_from_metadata(
        self,
        *,
        metadata: dict[str, Any],
        status: str,
        source: str,
        path: str,
        can_remove: bool = False,
        can_adopt: bool = False,
        origin_path: str | None = None,
        installed_at: str | None = None,
        error_message: str | None = None,
        install_source: str | None = None,
        name_override: str | None = None,
    ) -> SkillRecord:
        return SkillRecord(
            name=name_override or metadata["name"],
            description=metadata["description"],
            status=status,
            source=source,
            path=path,
            tools=tuple(metadata.get("tools") or ()),
            can_remove=can_remove,
            can_adopt=can_adopt,
            origin_path=origin_path,
            installed_at=installed_at,
            error_message=error_message,
            tool_name=metadata.get("tool_name"),
            triggers=tuple(metadata.get("triggers") or ()),
            intent_examples=tuple(metadata.get("intent_examples") or ()),
            input_schema=metadata.get("input_schema"),
            execution=metadata.get("execution"),
            interaction_mode=str(metadata.get("interaction_mode") or "react_tool"),
            requires_confirmation=bool(metadata.get("requires_confirmation", False)),
            compatibility=metadata.get("compatibility"),
            capabilities=tuple(metadata.get("capabilities") or ()),
            risk_level=str(metadata.get("risk_level") or "low"),
            install_source=install_source or metadata.get("install_source"),
            diagnostics=tuple(metadata.get("diagnostics") or ()),
        )

    def _load_workspace_records(self, registry: dict[str, Any]) -> list[SkillRecord]:
        items: list[SkillRecord] = []
        if not self.builtin_home.exists():
            return items
        imported_names = {
            name for name, entry in registry.get("skills", {}).items() if entry.get("source") == "imported"
        }
        for folder in sorted(self.builtin_home.iterdir(), key=lambda item: item.name):
            if not folder.is_dir():
                continue
            skill_md = folder / "SKILL.md"
            if not skill_md.exists():
                continue
            metadata = self._read_skill_metadata(folder, source="workspace", install_source=str(folder))
            if metadata["name"] in imported_names:
                continue
            override = registry.get("skills", {}).get(metadata["name"], {})
            status = str(override.get("status", "enabled"))
            items.append(
                self._record_from_metadata(
                    metadata=metadata,
                    status=status,
                    source="workspace",
                    path=str(folder),
                    can_adopt=True,
                    origin_path=str(folder),
                    install_source=str(folder),
                )
            )
        return items

    def _load_imported_records(self, registry: dict[str, Any]) -> list[SkillRecord]:
        items: list[SkillRecord] = []
        for name, entry in registry.get("skills", {}).items():
            if entry.get("source") != "imported":
                continue
            skill_dir = Path(str(entry.get("path", ""))).expanduser()
            status = str(entry.get("status", "enabled"))
            installed_at = str(entry.get("installed_at")) if entry.get("installed_at") else None
            origin_path = str(entry.get("origin_path")) if entry.get("origin_path") else None
            install_source = str(entry.get("install_source")) if entry.get("install_source") else origin_path
            try:
                security_review = review_skill_directory(skill_dir)
                if security_review.blocked:
                    raise DailyAgentError(security_block_message(security_review, subject=f"Skill {name} "))
                metadata = self._read_skill_metadata(skill_dir, source="imported", install_source=install_source)
                items.append(
                    self._record_from_metadata(
                        metadata=metadata,
                        status=status,
                        source="imported",
                        path=str(skill_dir),
                        can_remove=True,
                        origin_path=origin_path,
                        installed_at=installed_at,
                        install_source=install_source,
                        name_override=name,
                    )
                )
            except DailyAgentError as exc:
                items.append(
                    SkillRecord(
                        name=name,
                        description="这个导入 skill 的目录或 SKILL.md 已损坏。",
                        status="error",
                        source="imported",
                        path=str(skill_dir),
                        tools=(),
                        can_remove=True,
                        origin_path=origin_path,
                        installed_at=installed_at,
                        error_message=str(exc),
                        compatibility={"status": "error", "reason": str(exc)},
                        install_source=install_source,
                        diagnostics=(str(exc),),
                    )
                )
        return items

    def _load_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {"skills": {}}
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DailyAgentError(f"读取 skill 注册表失败：{exc}") from exc

    def _save_registry(self, payload: dict[str, Any]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _discover_skill_dirs(self, source_path: Path) -> list[Path]:
        discovered: list[Path] = []
        seen: set[Path] = set()
        if self._is_skill_dir(source_path):
            discovered.append(source_path)
            seen.add(source_path)
        for item in sorted(source_path.rglob("SKILL.md")):
            folder = item.parent
            if folder in seen:
                continue
            seen.add(folder)
            discovered.append(folder)
        return discovered

    def _read_skill_metadata(
        self,
        folder: Path,
        *,
        source: str = "workspace",
        install_source: str | None = None,
    ) -> dict[str, Any]:
        skill_md_path = folder / "SKILL.md"
        if not skill_md_path.exists():
            raise DailyAgentError(f"skill 目录缺少 SKILL.md：{folder}")
        content = skill_md_path.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(content)
        manifest = self._read_skill_manifest(folder)
        name = str(
            manifest.get("id")
            or manifest.get("name")
            or frontmatter.get("name")
            or frontmatter.get("id")
            or folder.name
        ).strip()
        if not VALID_SKILL_NAME_RE.match(name):
            raise DailyAgentError(
                f"skill 名称不合法：{name}。只允许小写字母、数字、下划线和中横线，且必须以字母或数字开头。"
            )
        manifest = _apply_trusted_adapter(name=name, manifest=manifest)
        description = str(
            manifest.get("description")
            or manifest.get("short_description")
            or frontmatter.get("description")
            or frontmatter.get("short_description")
            or ""
        ).strip() or f"{name} skill"
        compat = analyze_skill_compatibility(
            folder=folder,
            manifest=manifest,
            frontmatter=frontmatter,
            content=content,
            source=source,
            install_source=install_source,
            name=name,
            description=description,
        )
        raw_tool_name = (
            manifest.get("tool_name")
            or manifest.get("tool")
            or frontmatter.get("tool_name")
            or compat.get("compatible_tool_name")
        )
        tool_name = str(raw_tool_name).strip() if raw_tool_name else None
        triggers = _normalize_str_tuple(
            manifest.get("triggers")
            or manifest.get("commands")
            or frontmatter.get("triggers")
            or frontmatter.get("commands")
        )
        intent_examples = _normalize_str_tuple(
            manifest.get("intent_examples")
            or manifest.get("examples")
            or frontmatter.get("intent_examples")
            or frontmatter.get("examples")
        )
        input_schema = (
            manifest.get("input_schema")
            if isinstance(manifest.get("input_schema"), dict)
            else manifest.get("parameters")
            if isinstance(manifest.get("parameters"), dict)
            else None
        )
        if input_schema is None:
            input_schema = compat.get("compatible_input_schema")
        execution = (
            manifest.get("execution")
            if isinstance(manifest.get("execution"), dict)
            else manifest.get("runner")
            if isinstance(manifest.get("runner"), dict)
            else None
        )
        if execution is None:
            execution = compat.get("compatible_execution")
        requires_confirmation = bool(manifest.get("requires_confirmation", compat.get("requires_confirmation", False)))
        tools = tuple(
            _normalize_str_tuple(
                manifest.get("tools")
                or manifest.get("tool_names")
                or frontmatter.get("tools")
                or frontmatter.get("tool_names")
            )
        )
        if not tools and tool_name and execution:
            tools = (tool_name,)
        return {
            "name": name,
            "description": description,
            "tools": tools,
            "tool_name": tool_name,
            "triggers": triggers,
            "intent_examples": intent_examples,
            "input_schema": input_schema,
            "execution": execution,
            "interaction_mode": _normalize_interaction_mode(
                manifest.get("interaction_mode")
                or frontmatter.get("interaction_mode")
                or _infer_interaction_mode(
                    triggers=triggers,
                    input_schema=input_schema,
                    execution=execution,
                    requires_confirmation=requires_confirmation,
                )
            ),
            "requires_confirmation": requires_confirmation,
            "compatibility": compat.get("compatibility"),
            "capabilities": tuple(compat.get("capabilities") or ()),
            "risk_level": str(compat.get("risk_level") or "low"),
            "install_source": compat.get("install_source"),
            "diagnostics": tuple(compat.get("diagnostics") or ()),
        }

    def _read_skill_manifest(self, folder: Path) -> dict[str, Any]:
        """读取可选的 skill.json。

        `SKILL.md` 继续作为给人看的说明文档；
        `skill.json` 则承担“给程序执行”的职责。
        这样后续新增第三方 skill 时，不必再改主程序里的路由和执行分支。
        """
        manifest_path = self._locate_manifest_path(folder)
        if manifest_path is None:
            return {}
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DailyAgentError(f"读取 skill manifest 失败：{manifest_path} - {exc}") from exc
        if not isinstance(payload, dict):
            raise DailyAgentError(f"skill manifest 必须是 JSON 对象：{manifest_path}")
        return payload

    def _is_skill_dir(self, folder: Path) -> bool:
        return (folder / "SKILL.md").exists()

    def _locate_manifest_path(self, folder: Path) -> Path | None:
        candidates = (
            folder / "skill.json",
            folder / "manifest.json",
            folder / ".skill.json",
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None


def list_enabled_skill_paths() -> list[Path]:
    """给 agent 构建阶段复用，始终读取最新注册表。"""
    return SkillCatalog().enabled_skill_paths()


def list_enabled_tool_names() -> set[str]:
    """给执行器复用，确保启停状态无需重启即可生效。"""
    return SkillCatalog().enabled_tool_names()


def get_skill_name_for_tool(tool_name: str) -> str | None:
    return SkillCatalog().get_skill_name_for_tool(tool_name)


def list_enabled_skill_records() -> list[SkillRecord]:
    """给技能路由层复用，始终读取最新启用状态。"""
    return SkillCatalog().enabled_skill_records()


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """解析 SKILL.md 头部 frontmatter。

    这里只实现当前项目当前需要的扁平字段子集。
    目标不是完整 YAML 解析，而是尽量兼容常见第三方 skill frontmatter。
    """
    lines = content.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}

    frontmatter_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        frontmatter_lines.append(line)

    payload: dict[str, Any] = {}
    for raw_line in frontmatter_lines:
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip().strip('"').strip("'")
        if key in {
            "tools",
            "tool_names",
            "allowed-tools",
            "allowed_tools",
            "capabilities",
            "triggers",
            "commands",
            "intent_examples",
            "examples",
        }:
            payload[key] = _parse_tools_value(value)
        else:
            payload[key] = value
    return payload


def _parse_tools_value(value: str) -> tuple[str, ...]:
    normalized = value.strip()
    if not normalized:
        return ()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    items = [part.strip().strip('"').strip("'") for part in normalized.split(",")]
    return tuple(item for item in items if item)


def _normalize_str_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    items = [str(item).strip() for item in value if str(item).strip()]
    return tuple(items)


def _normalize_interaction_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"deterministic", "react_tool"}:
        return normalized
    return "react_tool"


def _apply_trusted_adapter(*, name: str, manifest: dict[str, Any]) -> dict[str, Any]:
    if name != "agent-browser":
        return manifest
    adapted = dict(manifest)
    adapted.update(
        {
            "tool_name": "agent_browser",
            "interaction_mode": "react_tool",
            "requires_confirmation": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open", "snapshot", "click", "fill", "press", "wait", "screenshot", "get_text", "get_title", "get_url", "close"],
                        "description": "单步浏览器动作。复杂任务必须拆成 open、snapshot、fill、click、wait、screenshot 等多轮调用。",
                    },
                    "url": {"type": "string", "description": "open 动作使用的网址。"},
                    "selector": {"type": "string", "description": "click、fill、wait、get_text 使用的元素引用或选择器。"},
                    "text": {"type": "string", "description": "fill、press 或 wait 使用的文本。"},
                    "path": {"type": "string", "description": "截图保存路径。"},
                    "full_page": {"type": "boolean", "description": "截图时是否包含完整页面。"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300, "default": 60},
                },
                "required": ["action"],
            },
            "execution": {
                "type": "python_callable",
                "entrypoint": "daily_agent.skill_handlers.agent_browser:run",
            },
        }
    )
    return adapted


def _infer_interaction_mode(
    *,
    triggers: tuple[str, ...],
    input_schema: dict[str, Any] | None,
    execution: dict[str, Any] | None,
    requires_confirmation: bool,
) -> str:
    _ = triggers
    if not execution:
        return "deterministic"
    _ = requires_confirmation
    required_fields = ()
    if isinstance(input_schema, dict):
        required_fields = tuple(input_schema.get("required") or ())
    if required_fields:
        return "deterministic"
    return "react_tool"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_github_url(value: str) -> bool:
    return bool(GITHUB_URL_RE.match(value.strip()))


def _is_skills_sh_url(value: str) -> bool:
    return bool(SKILLS_SH_URL_RE.match(value.strip()))


def _normalize_install_source(value: str) -> str:
    normalized = value.strip()
    if _is_github_url(normalized):
        return _normalize_github_url(normalized)
    return normalized


def _normalize_github_url(value: str) -> str:
    match = GITHUB_URL_RE.match(value.strip())
    if not match:
        raise DailyAgentError("只支持 https://github.com/owner/repo 或 git@github.com:owner/repo.git 格式。")
    owner = match.group("owner").lower()
    repo = match.group("repo").removesuffix(".git").lower()
    return f"https://github.com/{owner}/{repo}"


def _github_repo_name(value: str) -> str:
    match = GITHUB_URL_RE.match(value.strip())
    if not match:
        raise DailyAgentError("只支持 https://github.com/owner/repo 或 git@github.com:owner/repo.git 格式。")
    owner = match.group("owner").lower()
    repo = match.group("repo").removesuffix(".git").lower()
    safe = re.sub(r"[^a-z0-9_-]+", "-", f"{owner}-{repo}").strip("-")
    if not safe:
        raise DailyAgentError("GitHub 仓库名解析失败。")
    return safe


def _normalize_skill_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
    if len(normalized) > 64:
        normalized = normalized[:64].rstrip("-_")
    if not normalized or not VALID_SKILL_NAME_RE.match(normalized):
        return ""
    return normalized
