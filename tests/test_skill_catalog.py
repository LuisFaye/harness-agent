import json
from pathlib import Path

from daily_agent.app_context import ApplicationContext
from daily_agent.paths import AppPaths
from daily_agent.services.platform_service import SkillCatalogService
from daily_agent.skill_catalog import BUILTIN_SKILLS_HOME, SkillCatalog
from daily_agent.skill_isolation import prepare_skill_isolation


def test_skill_catalog_reads_manifest_metadata_for_runtime_skills(tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    skill_dir = imported_home / "daily-report"
    _write_skill(
        skill_dir,
        name="daily-report",
        description="生成日报",
        manifest={
            "id": "daily-report",
            "tool_name": "daily_report",
            "triggers": ["/daily-report", "生成日报"],
            "intent_examples": ["帮我生成日报"],
            "input_schema": {"type": "object", "properties": {"repos": {"type": "array"}}},
            "execution": {
                "type": "python_callable",
                "entrypoint": "daily_agent.skill_handlers.daily_report:run",
            },
        },
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        """{
  "skills": {
    "daily-report": {
      "source": "imported",
      "status": "enabled",
      "path": "%s"
    }
  }
}
"""
        % str(skill_dir),
        encoding="utf-8",
    )

    catalog = SkillCatalog(
        builtin_home=builtin_home,
        imported_home=imported_home,
        registry_path=registry_path,
    )

    item = catalog.list_skills()[0]

    assert item.tool_name == "daily_report"
    assert "/daily-report" in item.triggers
    assert item.intent_examples == ("帮我生成日报",)
    assert item.input_schema["type"] == "object"
    assert item.execution["entrypoint"] == "daily_agent.skill_handlers.daily_report:run"
    assert item.interaction_mode == "react_tool"


def test_builtin_skills_are_generic():
    catalog = SkillCatalog(
        builtin_home=BUILTIN_SKILLS_HOME,
        imported_home=Path("/tmp/not-used"),
        registry_path=Path("/tmp/not-used-registry.json"),
    )

    names = {item.name for item in catalog.list_skills()}

    assert "daily-report" in names
    assert "project-inspector" in names
    assert "official-document" in names
    assert "agent-browser" not in names
    assert "flight-deal" not in names
    assert "btc-strategy" not in names


def test_skill_detail_exposes_safe_internal_files_and_persistence_paths(tmp_path):
    paths = AppPaths.discover(data_dir=tmp_path / "data", workspace_root=tmp_path)
    builtin_home = tmp_path / "builtin"
    skill_dir = builtin_home / "demo"
    _write_skill(
        skill_dir,
        name="demo",
        description="查看内部文件",
        manifest={"id": "demo", "execution": {"type": "python_callable", "entrypoint": "demo:run"}},
    )
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text(
        'API_KEY = "sk-' + 'abcdefghijklmnop"\npassword: another-sensitive-value\nprint("ok")\n',
        encoding="utf-8",
    )
    (skill_dir / ".env").write_text("SHOULD_NOT_BE_VISIBLE=yes\n", encoding="utf-8")
    catalog = SkillCatalog(
        builtin_home=builtin_home,
        imported_home=paths.imported_skills_home,
        registry_path=paths.skill_registry_path,
    )
    service = SkillCatalogService(catalog=catalog, app_context=ApplicationContext.build(paths=paths))

    detail = service.get_skill_detail("demo")

    assert detail is not None
    files = {item["path"]: item for item in detail["files"]}
    assert {"SKILL.md", "skill.json", "scripts/run.py"} <= set(files)
    assert ".env" not in files
    assert 'API_KEY = "***"' in files["scripts/run.py"]["content"]
    assert "password: ***" in files["scripts/run.py"]["content"]
    assert "abcdefghijklmnop" not in files["scripts/run.py"]["content"]
    assert detail["persistence"]["status_registry_path"] == str(paths.skill_registry_path)


def test_removing_imported_skill_also_removes_private_runtime_directory(tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    source_dir = tmp_path / "source" / "demo-skill"
    _write_skill(source_dir, name="demo-skill", description="测试删除")
    catalog = SkillCatalog(
        builtin_home=builtin_home,
        imported_home=imported_home,
        registry_path=registry_path,
    )
    imported = catalog.import_local_path(str(source_dir))[0]
    isolation = prepare_skill_isolation(
        skill_name=imported.name,
        skill_directory=imported.path,
        user_id="u1",
        run_id="run-1",
        runtime_root=imported_home.parent / "runtime",
    )

    assert isolation.dependency_directory.is_dir()
    assert catalog.remove_skill(imported.name) is True
    assert isolation.dependency_directory.parent.exists() is False


def test_skill_catalog_supports_nested_skill_dirs_and_compatibility_metadata(tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    source_dir = tmp_path / "source"
    _write_skill(
        source_dir / "nested" / "demo-skill",
        name="demo-skill",
        description="兼容 skill",
        frontmatter_extra={"allowed-tools": "Bash(curl https://example.com)"},
    )
    catalog = SkillCatalog(
        builtin_home=builtin_home,
        imported_home=imported_home,
        registry_path=registry_path,
    )

    items = catalog.import_local_path(str(source_dir))
    item = items[0]

    assert item.name == "demo-skill"
    assert item.compatibility["status"] in {"compatible", "document_only"}
    assert item.risk_level in {"low", "medium", "high"}
    assert item.install_source == str(source_dir)
    assert item.to_dict()["compatibility"]["status"] == item.compatibility["status"]
    assert isinstance(item.diagnostics, tuple)


def test_skill_catalog_imports_github_url_via_clone(monkeypatch, tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    cloned_dir = tmp_path / "cloned"
    _write_skill(
        cloned_dir / "demo-skill",
        name="demo-skill",
        description="GitHub skill",
    )

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        target = Path(command[-1])
        target.mkdir(parents=True, exist_ok=True)
        _write_skill(target / "demo-skill", name="demo-skill", description="GitHub skill")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("daily_agent.skill_catalog.shutil.which", lambda name: "/usr/bin/git")
    monkeypatch.setattr("daily_agent.skill_catalog.subprocess.run", fake_run)

    catalog = SkillCatalog(
        builtin_home=builtin_home,
        imported_home=imported_home,
        registry_path=registry_path,
    )

    items = catalog.import_github_url("https://github.com/example/demo-skill")

    assert calls[0][:3] == ["/usr/bin/git", "clone", "--depth"]
    assert items[0].install_source == "https://github.com/example/demo-skill"
    assert items[0].name == "demo-skill"


def test_skill_catalog_returns_existing_records_when_github_url_already_imported(monkeypatch, tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        target = Path(command[-1])
        target.mkdir(parents=True, exist_ok=True)
        _write_skill(target / "demo-skill", name="demo-skill", description="GitHub skill")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("daily_agent.skill_catalog.shutil.which", lambda name: "/usr/bin/git")
    monkeypatch.setattr("daily_agent.skill_catalog.subprocess.run", fake_run)

    catalog = SkillCatalog(
        builtin_home=builtin_home,
        imported_home=imported_home,
        registry_path=registry_path,
    )

    first_items = catalog.import_github_url("https://github.com/example/demo-skill")
    second_items = catalog.import_github_url("https://github.com/example/demo-skill.git")

    assert len(calls) == 1
    assert [item.name for item in second_items] == [item.name for item in first_items]
    assert second_items[0].install_source == "https://github.com/example/demo-skill"


def test_skill_catalog_imports_one_skill_from_skills_sh_url(monkeypatch, tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"

    def fake_run(command, **kwargs):
        target = Path(command[-1])
        target.mkdir(parents=True, exist_ok=True)
        _write_skill(target / "skills" / "wanted", name="wanted", description="目标 skill")
        _write_skill(target / "skills" / "other", name="other", description="其他 skill")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("daily_agent.skill_catalog.shutil.which", lambda name: "/usr/bin/git")
    monkeypatch.setattr("daily_agent.skill_catalog.subprocess.run", fake_run)
    catalog = SkillCatalog(builtin_home=builtin_home, imported_home=imported_home, registry_path=registry_path)

    items = catalog.import_source("https://skills.sh/example/demo/wanted")

    assert [item.name for item in items] == ["wanted"]
    assert items[0].install_source == "https://skills.sh/example/demo/wanted"
    assert (imported_home / "wanted" / "SKILL.md").exists()
    assert not (imported_home / "other").exists()


def test_skill_catalog_renames_github_skill_when_builtin_name_conflicts(monkeypatch, tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    cloned_dir = tmp_path / "cloned"
    _write_skill(
        builtin_home / "agent-browser",
        name="agent-browser",
        description="内置浏览器 skill",
        manifest={
            "id": "agent-browser",
            "tool_name": "agent_browser",
            "execution": {
                "type": "python_callable",
                "entrypoint": "daily_agent.skill_handlers.agent_browser:run",
            },
        },
    )
    _write_skill(
        cloned_dir / "skills" / "agent-browser",
        name="agent-browser",
        description="GitHub skill",
    )

    def fake_run(command, **kwargs):
        target = Path(command[-1])
        target.mkdir(parents=True, exist_ok=True)
        _write_skill(target / "skills" / "agent-browser", name="agent-browser", description="GitHub skill")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("daily_agent.skill_catalog.shutil.which", lambda name: "/usr/bin/git")
    monkeypatch.setattr("daily_agent.skill_catalog.subprocess.run", fake_run)

    catalog = SkillCatalog(
        builtin_home=builtin_home,
        imported_home=imported_home,
        registry_path=registry_path,
    )

    items = catalog.import_github_url("https://github.com/vercel-labs/agent-browser")

    assert items[0].name != "agent-browser"
    assert items[0].name.startswith("vercel-labs-agent-browser")


def test_skill_catalog_uses_doc_only_when_no_exec_path(tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    skill_dir = tmp_path / "source" / "doc-skill"
    _write_skill(skill_dir, name="doc-skill", description="纯说明")
    catalog = SkillCatalog(builtin_home=builtin_home, imported_home=imported_home, registry_path=registry_path)
    catalog.import_local_path(str(skill_dir))

    item = catalog.list_skills()[0]

    assert item.compatibility["status"] == "document_only"
    assert item.execution is None
    assert item.input_schema is None
    assert item.diagnostics == ()


def test_skill_catalog_reads_explicit_interaction_mode(tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    skill_dir = imported_home / "project-inspector"
    _write_skill(
        skill_dir,
        name="project-inspector",
        description="项目结构检查",
        manifest={
            "id": "project-inspector",
            "tool_name": "project_inspector",
            "interaction_mode": "react_tool",
            "execution": {
                "type": "python_callable",
                "entrypoint": "daily_agent.skill_handlers.project_inspector:run",
            },
        },
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        """{
  "skills": {
    "project-inspector": {
      "source": "imported",
      "status": "enabled",
      "path": "%s"
    }
  }
}
"""
        % str(skill_dir),
        encoding="utf-8",
    )

    catalog = SkillCatalog(builtin_home=builtin_home, imported_home=imported_home, registry_path=registry_path)

    item = catalog.list_skills()[0]

    assert item.interaction_mode == "react_tool"


def test_skill_catalog_ignores_tool_pattern_declarations(tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    skill_dir = tmp_path / "source" / "browser-skill"
    _write_skill(
        skill_dir,
        name="browser-skill",
        description="浏览器 skill",
        frontmatter_extra={"allowed-tools": "agent-browser:*"},
    )
    catalog = SkillCatalog(builtin_home=builtin_home, imported_home=imported_home, registry_path=registry_path)
    catalog.import_local_path(str(skill_dir))

    item = catalog.list_skills()[0]

    assert item.compatibility["status"] != "unsupported"


def test_compatible_browser_skill_is_agent_callable_with_confirmation(tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    skill_dir = tmp_path / "source" / "agent-browser"
    _write_skill(
        skill_dir,
        name="agent-browser",
        description="浏览器自动化",
        frontmatter_extra={"allowed-tools": "Bash(agent-browser:*)"},
        body_lines=["agent-browser open https://example.com"],
    )
    catalog = SkillCatalog(builtin_home=builtin_home, imported_home=imported_home, registry_path=registry_path)
    catalog.import_local_path(str(skill_dir))

    item = catalog.list_skills()[0]

    assert item.requires_confirmation is True
    assert item.interaction_mode == "react_tool"
    assert item.execution == {
        "type": "python_callable",
        "entrypoint": "daily_agent.skill_handlers.agent_browser:run",
    }
    assert item.input_schema["required"] == ["action"]
    assert "chat" not in item.input_schema["properties"]["action"]["enum"]


def test_skill_catalog_only_detects_real_shell_command_lines(tmp_path):
    builtin_home = tmp_path / "builtin"
    imported_home = tmp_path / "managed" / "imported"
    registry_path = tmp_path / "managed" / "registry.json"
    skill_dir = tmp_path / "source" / "browser-skill"
    _write_skill(
        skill_dir,
        name="browser-skill",
        description="agent-browser usage guide. Read this before running any agent-browser commands.",
        frontmatter_extra={"allowed-tools": "Bash(agent-browser:*)"},
        body_lines=[
            "agent-browser core",
            "agent-browser open <url> # 1. Open a page",
            "普通说明 agent-browser click @e3 不应该被当成命令",
        ],
    )
    catalog = SkillCatalog(builtin_home=builtin_home, imported_home=imported_home, registry_path=registry_path)
    catalog.import_local_path(str(skill_dir))

    item = catalog.list_skills()[0]

    assert item.execution["detected_shell_commands"] == ["agent-browser open <url>"]


def _write_skill(
    folder: Path,
    *,
    name: str,
    description: str,
    manifest: dict | None = None,
    manifest_name: str = "skill.json",
    frontmatter_extra: dict[str, str] | None = None,
    body_lines: list[str] | None = None,
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    extra_lines = []
    for key, value in (frontmatter_extra or {}).items():
        extra_lines.append(f"{key}: {value}")
    folder.joinpath("SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f'description: "{description}"',
                *extra_lines,
                "---",
                "",
                f"# {name}",
                *(body_lines or []),
            ]
        ),
        encoding="utf-8",
    )
    if manifest is not None:
        folder.joinpath(manifest_name).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
