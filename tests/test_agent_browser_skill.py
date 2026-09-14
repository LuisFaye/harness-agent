import subprocess

from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_handlers import agent_browser
from daily_agent.skill_runtime import SKILL_STATUS_ERROR, SKILL_STATUS_SUCCESS, SkillExecutionContext


def test_agent_browser_handler_runs_open_action(monkeypatch):
    calls = []

    monkeypatch.setattr(agent_browser.shutil, "which", lambda name: "/usr/local/bin/agent-browser")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="opened", stderr="")

    monkeypatch.setattr(agent_browser.subprocess, "run", fake_run)

    result = agent_browser.run(
        context=_context(
            inputs={"action": "open", "url": "https://example.com", "timeout_seconds": 5},
            query="打开 example.com",
        )
    )

    assert result.status == SKILL_STATUS_SUCCESS
    assert calls[0][0] == ["/usr/local/bin/agent-browser", "open", "https://example.com"]
    assert calls[0][1]["timeout"] == 5
    assert "opened" in result.response_text


def test_agent_browser_handler_reports_missing_cli(monkeypatch):
    monkeypatch.setattr(agent_browser.shutil, "which", lambda name: None)

    result = agent_browser.run(context=_context(inputs={"action": "snapshot"}))

    assert result.status == SKILL_STATUS_ERROR
    assert "npm install -g agent-browser" in result.response_text


def test_agent_browser_handler_requires_explicit_primitive_action(monkeypatch):
    monkeypatch.setattr(agent_browser.shutil, "which", lambda name: "/usr/local/bin/agent-browser")

    result = agent_browser.run(context=_context(inputs={}, query="打开 https://example.com 并总结页面"))

    assert result.status == SKILL_STATUS_ERROR
    assert "必须明确指定 action" in result.response_text


def test_agent_browser_handler_rejects_chat_without_gateway_key(monkeypatch):
    monkeypatch.setattr(agent_browser.shutil, "which", lambda name: "/usr/local/bin/agent-browser")
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)

    result = agent_browser.run(
        context=_context(inputs={"action": "chat", "instruction": "打开 example.com"})
    )

    assert result.status == SKILL_STATUS_ERROR
    assert "单步动作" in result.response_text


def _context(inputs=None, query=""):
    return SkillExecutionContext(
        query=query,
        user_id="u1",
        session_id="s1",
        skill=SkillRecord(
            name="agent-browser",
            description="浏览器自动化",
            status="enabled",
            source="workspace",
            path="/tmp/agent-browser",
            tools=("agent_browser",),
        ),
        working_directory="/tmp",
        inputs=dict(inputs or {}),
        state={},
    )
