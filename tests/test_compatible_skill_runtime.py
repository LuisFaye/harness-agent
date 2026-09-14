import json
import platform
import shutil
import uuid
from pathlib import Path

import pytest

from daily_agent.compatible_skill_runtime import CompatibleSkillRuntime
from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_isolation import prepare_skill_isolation
from daily_agent.skill_runtime import SKILL_STATUS_ERROR, SKILL_STATUS_SUCCESS, SkillExecutionContext
from daily_agent.skill_runtime import SkillRuntime


def test_compatible_runtime_rejects_dangerous_shell_command(tmp_path):
    skill = SkillRecord(
        name="danger",
        description="危险命令测试",
        status="enabled",
        source="imported",
        path=str(tmp_path),
        tools=("danger",),
        tool_name="danger",
        capabilities=("shell",),
        execution={
            "type": "compatible_skill",
            "skill_dir": str(tmp_path),
            "capabilities": ["shell"],
        },
    )

    result = CompatibleSkillRuntime().run(
        context=_context(
            skill=skill,
            inputs={"inputs": {"command": "rm -rf /"}},
            working_directory=str(tmp_path),
        )
    )

    assert result.status == SKILL_STATUS_ERROR
    assert "安全拦截" in result.response_text


def test_compatible_runtime_runs_http_get(monkeypatch, tmp_path):
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def request(self, method, url, headers=None, data=None):
            request = httpx.Request(method, url)
            return httpx.Response(200, text="hello", request=request, headers={"content-type": "text/plain"})

    monkeypatch.setattr("daily_agent.compatible_skill_runtime.httpx.Client", FakeClient)
    skill = SkillRecord(
        name="fetcher",
        description="网页读取",
        status="enabled",
        source="imported",
        path=str(tmp_path),
        tools=("fetcher",),
        tool_name="fetcher",
        capabilities=("http", "web_fetch"),
        execution={
            "type": "compatible_skill",
            "skill_dir": str(tmp_path),
            "capabilities": ["http", "web_fetch"],
        },
    )

    result = CompatibleSkillRuntime().run(
        context=_context(
            skill=skill,
            inputs={"url": "https://example.com", "timeout_seconds": 3},
            working_directory=str(tmp_path),
        )
    )

    assert result.status == SKILL_STATUS_SUCCESS
    assert result.output_text == "hello"
    assert result.observation["status_code"] == 200


def test_compatible_runtime_prefers_explicit_agent_browser_command(monkeypatch, tmp_path):
    import subprocess

    calls = []

    monkeypatch.setattr("daily_agent.compatible_skill_runtime.shutil.which", lambda name: f"/usr/local/bin/{name}")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="opened", stderr="")

    monkeypatch.setattr("daily_agent.compatible_skill_runtime.subprocess.run", fake_run)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    skill = SkillRecord(
        name="core",
        description="浏览器操作",
        status="enabled",
        source="imported",
        path=str(tmp_path),
        tools=("core",),
        tool_name="core",
        capabilities=("browser", "shell"),
        execution={
            "type": "compatible_skill",
            "skill_dir": str(tmp_path),
            "capabilities": ["browser", "shell"],
        },
    )

    result = CompatibleSkillRuntime().run(
        context=_context(
            skill=skill,
            inputs={"inputs": {"command": "agent-browser open https://example.com"}, "timeout_seconds": 5},
            working_directory=str(tmp_path),
        )
    )

    assert result.status == SKILL_STATUS_SUCCESS
    assert calls[0][0] == ["/usr/local/bin/agent-browser", "open", "https://example.com"]
    assert calls[0][1]["timeout"] == 5
    assert result.output_text == "opened"


def test_compatible_shell_uses_private_environment_without_main_secrets(monkeypatch, tmp_path):
    import subprocess

    calls = []
    monkeypatch.setenv("SB_AGENT_MODEL_API_KEY", "should-not-leak")
    monkeypatch.setenv("SB_AGENT_MYSQL_PASSWORD", "should-not-leak")
    monkeypatch.setattr("daily_agent.compatible_skill_runtime.shutil.which", lambda name: f"/usr/local/bin/{name}")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    monkeypatch.setattr("daily_agent.compatible_skill_runtime.subprocess.run", fake_run)
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    isolation = prepare_skill_isolation(
        skill_name="isolated-node",
        skill_directory=skill_dir,
        user_id="u1",
        run_id="run-1",
        runtime_root=tmp_path / "runtime",
    )
    skill = SkillRecord(
        name="isolated-node",
        description="隔离环境测试",
        status="enabled",
        source="imported",
        path=str(skill_dir),
        tools=("isolated-node",),
        tool_name="isolated-node",
        capabilities=("shell",),
        execution={"type": "compatible_skill", "skill_dir": str(skill_dir), "capabilities": ["shell"]},
    )
    context = _context(
        skill=skill,
        inputs={"inputs": {"command": "node demo.js"}},
        working_directory=str(tmp_path),
        isolation=isolation,
    )

    result = CompatibleSkillRuntime().run(context=context)

    environment = calls[0][1]["env"]
    assert result.status == SKILL_STATUS_SUCCESS
    assert environment["HOME"] == str(isolation.home_directory)
    assert environment["TMPDIR"] == str(isolation.temporary_directory)
    assert environment["NODE_PATH"] == str(isolation.node_dependency_directory)
    assert environment["SB_AGENT_SKILL_RESOURCE_DIR"] == str(isolation.resource_directory)
    assert "SB_AGENT_MODEL_API_KEY" not in environment
    assert "SB_AGENT_MYSQL_PASSWORD" not in environment
    assert result.observation["isolation"] in {"filesystem_sandbox", "private_process"}
    isolation.cleanup_temporary()


def test_real_node_skill_process_only_sees_private_runtime_environment(monkeypatch, tmp_path):
    if not shutil.which("node"):
        pytest.skip("本机没有 Node.js，跳过真实 Skill 子进程测试。")
    monkeypatch.setenv("SB_AGENT_MODEL_API_KEY", "should-not-leak")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    skill_dir.joinpath("inspect-env.mjs").write_text(
        "console.log(JSON.stringify({home: process.env.HOME, temp: process.env.TMPDIR, "
        "data: process.env.SB_AGENT_SKILL_DATA_DIR, leaked: Boolean(process.env.SB_AGENT_MODEL_API_KEY)}));",
        encoding="utf-8",
    )
    skill = SkillRecord(
        name="real-isolated-node",
        description="真实隔离进程测试",
        status="enabled",
        source="imported",
        path=str(skill_dir),
        tools=("real-isolated-node",),
        tool_name="real-isolated-node",
        capabilities=("shell",),
        execution={"type": "compatible_skill", "skill_dir": str(skill_dir), "capabilities": ["shell"]},
    )
    runtime_root = tmp_path / "runtime"
    runtime = SkillRuntime(isolation_root=str(runtime_root))

    result = runtime.run(
        skill=skill,
        query="检查环境",
        user_id="u1",
        session_id="s1",
        run_id="run-real",
        inputs={"inputs": {"command": "node inspect-env.mjs"}},
    )

    payload = json.loads(result.output_text)
    assert result.status == SKILL_STATUS_SUCCESS
    assert payload["home"].startswith(str(runtime_root))
    assert payload["temp"].startswith(str(runtime_root))
    assert payload["data"].startswith(str(runtime_root))
    assert payload["leaked"] is False
    assert not list(runtime_root.rglob("run-real-*"))


def test_macos_filesystem_sandbox_blocks_unscoped_home_file(monkeypatch, tmp_path):
    if platform.system() != "Darwin" or not shutil.which("sandbox-exec") or not shutil.which("node"):
        pytest.skip("当前系统没有可用的 macOS 文件沙箱。")
    secret_path = Path.home() / f".sb-agent-sandbox-secret-{uuid.uuid4().hex}"
    secret_path.write_text("private", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    project_dir = tmp_path / "project"
    skill_dir.mkdir()
    project_dir.mkdir()
    project_secret = project_dir / ".env"
    project_secret.write_text("MODEL_KEY=private", encoding="utf-8")
    skill_dir.joinpath("read-secret.mjs").write_text(
        "import fs from 'node:fs'; let homeReadable = true; let projectSecretReadable = true; "
        f"try {{ fs.readFileSync({json.dumps(str(secret_path))}, 'utf8'); }} catch {{ homeReadable = false; }} "
        f"try {{ fs.readFileSync({json.dumps(str(project_secret))}, 'utf8'); }} catch {{ projectSecretReadable = false; }} "
        "console.log(JSON.stringify({homeReadable, projectSecretReadable}));",
        encoding="utf-8",
    )
    skill = SkillRecord(
        name="sandboxed-node",
        description="文件沙箱测试",
        status="enabled",
        source="imported",
        path=str(skill_dir),
        tools=("sandboxed-node",),
        tool_name="sandboxed-node",
        capabilities=("shell",),
        execution={"type": "compatible_skill", "skill_dir": str(skill_dir), "capabilities": ["shell"]},
    )
    runtime = SkillRuntime(working_directory=str(project_dir), isolation_root=str(tmp_path / "runtime"))

    try:
        result = runtime.run(
            skill=skill,
            query="读取越界文件",
            user_id="u1",
            session_id="s1",
            run_id="run-sandbox",
            inputs={"inputs": {"command": "node read-secret.mjs"}},
        )
    finally:
        secret_path.unlink(missing_ok=True)

    assert result.status == SKILL_STATUS_SUCCESS
    payload = json.loads(result.output_text)
    assert payload["homeReadable"] is False
    assert payload["projectSecretReadable"] is False
    assert result.observation["isolation"] == "filesystem_sandbox"


def test_compatible_runtime_does_not_default_to_agent_browser_chat_without_gateway_key(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    skill = SkillRecord(
        name="core",
        description="浏览器操作",
        status="enabled",
        source="imported",
        path=str(tmp_path),
        tools=("core",),
        tool_name="core",
        capabilities=("browser",),
        execution={
            "type": "compatible_skill",
            "skill_dir": str(tmp_path),
            "capabilities": ["browser"],
        },
    )

    result = CompatibleSkillRuntime().run(
        context=_context(
            skill=skill,
            inputs={"task": "打开 example.com"},
            working_directory=str(tmp_path),
        )
    )

    assert result.status == SKILL_STATUS_ERROR
    assert "inputs.command" in result.response_text
    assert "AI_GATEWAY_API_KEY" in result.response_text


def _context(skill, inputs, working_directory, isolation=None):
    return SkillExecutionContext(
        query="测试",
        user_id="u1",
        session_id="s1",
        skill=skill,
        working_directory=working_directory,
        inputs=inputs,
        state={},
        isolation=isolation,
    )
