import pytest

from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_memory import SkillPreference, SkillPrivateMemory
from daily_agent.skill_runtime import SKILL_STATUS_SUCCESS, SkillRunResult, SkillRuntime


class RecordingRepository:
    def __init__(self):
        self.values = {}

    def set(self, user_id, skill_name, key, value):
        self.values[(user_id, skill_name, key)] = value

    def list(self, user_id, skill_name):
        return [
            SkillPreference(key=key, value=value)
            for (saved_user, saved_skill, key), value in self.values.items()
            if saved_user == user_id and saved_skill == skill_name
        ]

    def delete(self, user_id, skill_name, key):
        return self.values.pop((user_id, skill_name, key), None) is not None


def test_skill_private_memory_is_isolated_by_user_and_skill():
    repository = RecordingRepository()
    first = SkillPrivateMemory(user_id="u1", skill_name="daily-report", repository=repository)
    second = SkillPrivateMemory(user_id="u1", skill_name="project-inspector", repository=repository)

    first.set("format", "简洁")
    second.set("format", "详细")

    assert first.get("format") == "简洁"
    assert second.get("format") == "详细"
    assert first.delete("format") is True
    assert first.get("format") is None
    assert second.get("format") == "详细"


def test_skill_private_memory_rejects_secret_values_but_accepts_environment_name():
    memory = SkillPrivateMemory(user_id="u1", skill_name="demo", repository=RecordingRepository())

    with pytest.raises(ValueError, match="不能保存密钥明文"):
        memory.set("api_key", "sk-secret")

    memory.set("api_key_env", "DEMO_API_KEY")
    assert memory.get("api_key_env") == "DEMO_API_KEY"


def test_skill_runtime_injects_scoped_private_memory(monkeypatch, tmp_path):
    repository = RecordingRepository()
    runtime = SkillRuntime(
        skill_memory_repository=repository,
        isolation_root=str(tmp_path / "runtime"),
    )
    skill = SkillRecord(
        name="demo-skill",
        description="测试 Skill 私有记忆",
        status="enabled",
        source="workspace",
        path="/tmp/demo-skill",
        tools=(),
        execution={"type": "python_callable", "entrypoint": "demo:run"},
    )

    def handler(*, context):
        context.private_memory.set("choice", "A")
        return SkillRunResult(
            status=SKILL_STATUS_SUCCESS,
            response_text=context.private_memory.get("choice"),
            stop_reason="完成",
        )

    monkeypatch.setattr(runtime, "_load_callable", lambda entrypoint: handler)

    result = runtime.run(skill=skill, query="保存选择", user_id="u1", session_id="s1")

    assert result.response_text == "A"
    assert repository.values == {("u1", "demo-skill", "choice"): "A"}


def test_skill_runtime_injects_private_paths_and_removes_run_temp(monkeypatch, tmp_path):
    skill_dir = tmp_path / "demo-skill"
    resources = skill_dir / "resources"
    resources.mkdir(parents=True)
    captured = {}
    runtime = SkillRuntime(isolation_root=str(tmp_path / "runtime"))
    skill = SkillRecord(
        name="demo-skill",
        description="测试 Skill 目录隔离",
        status="enabled",
        source="imported",
        path=str(skill_dir),
        tools=(),
        execution={"type": "python_callable", "entrypoint": "demo:run"},
    )

    def handler(*, context):
        captured["isolation"] = context.isolation
        context.isolation.temporary_directory.joinpath("working.txt").write_text("ok", encoding="utf-8")
        context.isolation.data_directory.joinpath("saved.txt").write_text("ok", encoding="utf-8")
        return SkillRunResult(status=SKILL_STATUS_SUCCESS, response_text="ok", stop_reason="完成")

    monkeypatch.setattr(runtime, "_load_callable", lambda entrypoint: handler)

    result = runtime.run(
        skill=skill,
        query="测试隔离",
        user_id="u1",
        session_id="s1",
        run_id="run-1",
    )

    isolation = captured["isolation"]
    assert result.status == SKILL_STATUS_SUCCESS
    assert isolation.resource_directory == resources
    assert isolation.data_directory.joinpath("saved.txt").read_text(encoding="utf-8") == "ok"
    assert isolation.python_dependency_directory.is_dir()
    assert isolation.node_dependency_directory.is_dir()
    assert isolation.temporary_directory.exists() is False
