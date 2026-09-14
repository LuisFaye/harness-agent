import asyncio
import sys
import socket
import subprocess
import time
from pathlib import Path

import pytest

from daily_agent.agent import _connected_mcp_tools
from daily_agent.capability_models import McpServerWriteRequest
from daily_agent.errors import DailyAgentError
from daily_agent.mcp_catalog import McpCatalog, McpServerRecord
from daily_agent.mcp_runtime import McpRuntimeManager, _ConnectedServer, _plain_error, _resolve_environment_values
from daily_agent.services.capability_service import McpService, ToolService
from daily_agent.skill_catalog import SkillRecord
from daily_agent.tool_registry import ToolRegistry, skill_tool_id


class _SkillCatalogStub:
    def __init__(self, items=None):
        self.items = list(items or [])

    def list_skills(self):
        return list(self.items)


class _McpCatalogStub:
    def __init__(self, items=None):
        self.items = list(items or [])

    def list_servers(self):
        return list(self.items)


def _skill():
    return SkillRecord(
        name="project-inspector",
        description="检查项目",
        status="enabled",
        source="workspace",
        path="/tmp/project-inspector",
        tools=("project_inspector",),
        tool_name="project_inspector",
        input_schema={"type": "object", "properties": {}},
        execution={"type": "python_callable", "entrypoint": "demo:run"},
        interaction_mode="react_tool",
        compatibility={"status": "native"},
    )


def test_tool_registry_uses_real_skill_runtime_state_and_independent_toggle(tmp_path):
    skill = _skill()
    registry = ToolRegistry(
        path=tmp_path / "tools.json",
        skill_catalog=_SkillCatalogStub([skill]),
        mcp_catalog=_McpCatalogStub(),
    )

    before = registry.list_tools()[0]
    registry.set_enabled(skill_tool_id(skill), False)
    after = registry.list_tools()[0]

    assert before.available is True
    assert after.enabled is False
    assert after.available is False
    assert after.unavailable_reason == "对应 Tool 已单独停用。"


def test_mcp_catalog_never_persists_environment_values(tmp_path):
    catalog = McpCatalog(path=tmp_path / "mcp.json")
    record = catalog.create(
        McpServerWriteRequest(
            name="企业查询",
            transport_type="streamable_http",
            url="https://example.test/mcp",
            environment_keys=["MCP_TOKEN"],
            header_env_map={"Authorization": "MCP_TOKEN"},
        )
    )

    saved = (tmp_path / "mcp.json").read_text(encoding="utf-8")
    assert record.environment_keys == ["MCP_TOKEN"]
    assert "MCP_TOKEN" in saved
    assert "Bearer secret" not in saved


def test_mcp_runtime_can_read_secret_from_private_config(monkeypatch):
    monkeypatch.setattr(
        "daily_agent.mcp_runtime.load_secret_value",
        lambda name: "Bearer local-secret" if name == "MCP_TOKEN" else None,
    )

    assert _resolve_environment_values(["MCP_TOKEN", "MISSING"]) == {
        "MCP_TOKEN": "Bearer local-secret"
    }


def test_mcp_service_reconnects_enabled_servers_and_keeps_startup_alive_on_failure(tmp_path):
    class Runtime:
        def __init__(self):
            self.connected = set()

        def connect(self, record, force=False):
            if "失败" in record.name:
                raise DailyAgentError("模拟连接失败")
            self.connected.add(record.server_id)
            return {
                "tools": [{"name": "echo", "description": "测试工具", "input_schema": {}}],
                "resources": [],
                "prompts": [],
            }

        def is_connected(self, server_id):
            return server_id in self.connected

    catalog = McpCatalog(path=tmp_path / "mcp.json")
    working = catalog.create(
        McpServerWriteRequest(name="正常服务", transport_type="stdio", command=sys.executable)
    )
    failing = catalog.create(
        McpServerWriteRequest(name="失败服务", transport_type="stdio", command=sys.executable)
    )
    disabled = catalog.create(
        McpServerWriteRequest(name="停用服务", transport_type="stdio", command=sys.executable, enabled=False)
    )
    service = McpService(catalog=catalog, runtime=Runtime())

    results = service.connect_enabled_servers()

    by_id = {item.server_id: item for item in results}
    assert by_id[working.server_id].status == "connected"
    assert by_id[failing.server_id].status == "error"
    assert disabled.server_id not in by_id


def test_mcp_service_rejects_connection_without_any_capability(tmp_path):
    class Runtime:
        def __init__(self):
            self.connected = set()

        def connect(self, record, force=False):
            self.connected.add(record.server_id)
            return {"tools": [], "resources": [], "prompts": []}

        def disconnect(self, server_id):
            self.connected.discard(server_id)
            return True

        def is_connected(self, server_id):
            return server_id in self.connected

    catalog = McpCatalog(path=tmp_path / "mcp.json")
    record = catalog.create(
        McpServerWriteRequest(name="空能力服务", transport_type="stdio", command=sys.executable)
    )
    service = McpService(catalog=catalog, runtime=Runtime())

    try:
        service.connect_server(record.server_id)
        assert False, "空能力 MCP 应当连接失败"
    except DailyAgentError as exc:
        assert "没有发现任何 Tool、Resource 或 Prompt" in str(exc)

    failed = service.get_server(record.server_id)
    assert failed is not None
    assert failed.status == "error"
    assert failed.tools == []
    assert service.runtime.is_connected(record.server_id) is False


def test_mcp_catalog_normalizes_json_array_fragments_in_stdio_args(tmp_path):
    catalog = McpCatalog(path=tmp_path / "mcp.json")
    record = catalog.create(
        McpServerWriteRequest(
            name="MySQL",
            transport_type="stdio",
            command="mysql_mcp_server_pro",
            args=['"--mode",', '"stdio",', '"--oauth",', '"false"'],
        )
    )

    assert record.args == ["--mode", "stdio", "--oauth", "false"]
    assert catalog.get(record.server_id).args == ["--mode", "stdio", "--oauth", "false"]


def test_stdio_mcp_connects_syncs_and_invokes_real_tool(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    runtime = McpRuntimeManager()
    catalog = McpCatalog(path=tmp_path / "mcp.json")
    record = catalog.create(
        McpServerWriteRequest(
            name="Echo Test",
            transport_type="stdio",
            command=sys.executable,
            args=[str(fixture)],
        )
    )
    service = McpService(catalog=catalog, runtime=runtime)

    connected = service.connect_server(record.server_id)
    synced = service.connect_server(record.server_id, force=True)
    tool_name = synced.tools[0]["name"]
    result = runtime.invoke(record.server_id, tool_name, {"text": "你好"})
    resource = runtime.read_resource(record.server_id, "echo://status")
    prompt = runtime.render_prompt(record.server_id, "greeting_prompt", {"name": "小林"})
    agent_tools = runtime.build_agent_tools()
    resource_tool = next(item for item in agent_tools if "_resource_" in item.name)
    prompt_tool = next(item for item in agent_tools if "_prompt_" in item.name)
    resource_from_agent = resource_tool.entrypoint()
    prompt_from_agent = prompt_tool.entrypoint(name="小林")
    disconnected = service.disconnect_server(record.server_id)

    assert connected.status == "connected"
    assert synced.status == "connected"
    assert connected.tools[0]["description"]
    assert connected.resources[0]["uri"] == "echo://status"
    assert connected.prompts[0]["name"] == "greeting_prompt"
    assert connected.prompts[0]["arguments"][0]["name"] == "name"
    assert result == "MCP-ECHO:你好"
    assert resource[0]["text"] == "ready"
    assert prompt["messages"][0]["content"]["text"] == "请向小林问好。"
    assert resource_from_agent["contents"][0]["text"] == "ready"
    assert prompt_from_agent["messages"][0]["content"]["text"] == "请向小林问好。"
    assert disconnected.status == "disconnected"


def test_stdio_mcp_automatically_recovers_after_server_process_crashes(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    marker = tmp_path / "crashed-once.txt"
    runtime = McpRuntimeManager()
    record = McpServerRecord(
        server_id="mcp-auto-recover",
        name="Recover Integration",
        transport_type="stdio",
        command=sys.executable,
        args=[str(fixture), "--crash-once", str(marker)],
    )
    capabilities = runtime.connect(record)
    tool_name = next(item["name"] for item in capabilities["tools"] if item["name"].endswith("unstable_echo"))

    result = runtime.invoke(record.server_id, tool_name, {"text": "恢复成功"})

    assert marker.exists()
    assert result == "MCP-RECOVERED:恢复成功"
    assert runtime.is_connected(record.server_id) is True
    runtime.disconnect(record.server_id)


def test_connected_mcp_tool_is_wrapped_for_agent_and_can_be_disabled(tmp_path, monkeypatch):
    fixture = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    runtime = McpRuntimeManager()
    record = McpServerRecord(
        server_id="mcp-echo",
        name="Echo Test",
        transport_type="stdio",
        command=sys.executable,
        args=[str(fixture)],
    )
    capabilities = runtime.connect(record)
    record.status = "connected"
    record.tools = capabilities["tools"]
    registry = ToolRegistry(
        path=tmp_path / "tools.json",
        skill_catalog=_SkillCatalogStub(),
        mcp_catalog=_McpCatalogStub([record]),
    )
    monkeypatch.setattr("daily_agent.agent.get_mcp_runtime", lambda: runtime)
    monkeypatch.setattr("daily_agent.agent.ToolRegistry", lambda: registry)

    enabled_tools = _connected_mcp_tools()
    registry.set_enabled(f"mcp:{record.server_id}:{capabilities['tools'][0]['name']}", False)
    disabled_tools = _connected_mcp_tools()
    runtime.disconnect(record.server_id)

    enabled_names = [item.name for item in enabled_tools]
    disabled_names = [item.name for item in disabled_tools]
    assert enabled_names[:2] == [item["name"] for item in capabilities["tools"]]
    assert any("_resource_" in name for name in enabled_names)
    assert any("_prompt_" in name for name in enabled_names)
    assert capabilities["tools"][0]["name"] not in disabled_names
    assert capabilities["tools"][1]["name"] in disabled_names
    assert any("_resource_" in name for name in disabled_names)
    assert any("_prompt_" in name for name in disabled_names)


def test_streamable_http_mcp_connects_and_invokes(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, str(fixture), "--http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        runtime = McpRuntimeManager()
        record = McpServerRecord(
            server_id="mcp-http",
            name="HTTP Echo",
            transport_type="streamable_http",
            url=f"http://127.0.0.1:{port}/mcp",
        )
        capabilities = runtime.connect(record)
        tool_name = capabilities["tools"][0]["name"]

        assert runtime.invoke(record.server_id, tool_name, {"text": "HTTP"}) == "MCP-ECHO:HTTP"
        assert runtime.disconnect(record.server_id) is True
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_mcp_runtime_translates_common_connection_error():
    message = _plain_error(Exception("MCP command needs to use one of the following executables"))

    assert message == "MCP 启动命令不受支持，请使用 python、node、npx、docker 等可执行程序。"


def test_mcp_runtime_treats_wrapped_timeout_text_as_tool_failure():
    class Function:
        description = "测试"
        parameters = {}
        requires_confirmation = False

        @staticmethod
        def entrypoint(**kwargs):
            return (
                "MCP tool 'execute_sql' failed: Timed out while waiting for response "
                "to ClientRequest. Waited 15.0 seconds."
            )

    class Toolkit:
        async def close(self):
            return None

    runtime = McpRuntimeManager()
    record = McpServerRecord(
        server_id="mcp-timeout",
        name="Timeout Test",
        transport_type="stdio",
        command=sys.executable,
    )
    runtime._servers[record.server_id] = _ConnectedServer(
        record=record,
        toolkit=Toolkit(),
        functions={"execute_sql": Function()},
        capabilities={"tools": [], "resources": [], "prompts": []},
    )

    with pytest.raises(DailyAgentError, match="MCP Tool 调用超时"):
        runtime.invoke(record.server_id, "execute_sql", {"query": "SELECT 1"})


def test_mcp_runtime_reconnects_once_after_connection_break(monkeypatch):
    class Function:
        description = "测试"
        parameters = {}
        requires_confirmation = False

        def __init__(self, entrypoint):
            self.entrypoint = entrypoint

    class Toolkit:
        async def close(self):
            return None

    runtime = McpRuntimeManager()
    record = McpServerRecord(
        server_id="mcp-recover",
        name="Recover Test",
        transport_type="stdio",
        command=sys.executable,
    )
    runtime._servers[record.server_id] = _ConnectedServer(
        record=record,
        toolkit=Toolkit(),
        functions={"echo": Function(lambda **kwargs: (_ for _ in ()).throw(Exception("Connection closed")))},
        capabilities={"tools": [], "resources": [], "prompts": []},
    )

    async def reconnect(reconnect_record, force):
        runtime._servers[reconnect_record.server_id] = _ConnectedServer(
            record=reconnect_record,
            toolkit=Toolkit(),
            functions={"echo": Function(lambda **kwargs: f"RECOVERED:{kwargs['text']}")},
            capabilities={"tools": [], "resources": [], "prompts": []},
        )
        return runtime._servers[reconnect_record.server_id].capabilities

    monkeypatch.setattr(runtime, "_connect", reconnect)

    result = asyncio.run(runtime._invoke(record.server_id, "echo", {"text": "你好"}))

    assert result == "RECOVERED:你好"
