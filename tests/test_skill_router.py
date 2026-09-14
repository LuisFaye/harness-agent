from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_router import SkillRouter
from daily_agent.skill_slot_filling import SkillSlotFiller, build_missing_info_prompt


def test_skill_router_does_not_select_daily_report_by_generic_help_words():
    skill = SkillRecord(
        name="daily-report",
        description="根据 git 提交记录生成工作日报。",
        status="enabled",
        source="workspace",
        path="/tmp/daily-report",
        tools=("scan_git_commits",),
        triggers=("/daily-report", "日报", "工作总结"),
        intent_examples=("帮我生成今天的日报", "总结今天做了什么"),
        input_schema={
            "type": "object",
            "properties": {
                "repos": {"type": "array", "title": "仓库路径", "description": "要扫描的 git 仓库路径"},
            },
            "required": ["repos"],
        },
        execution={"type": "python_callable", "entrypoint": "daily_agent.skill_handlers.daily_report:run"},
    )

    decision = SkillRouter().route(query="帮我梳理当前项目结构，并说明聊天和调度两条主流程", candidates=[skill])

    assert decision is None


def test_skill_router_selects_daily_report_for_report_query():
    skill = SkillRecord(
        name="daily-report",
        description="根据 git 提交记录生成工作日报。",
        status="enabled",
        source="workspace",
        path="/tmp/daily-report",
        tools=("scan_git_commits",),
        triggers=("/daily-report", "日报", "工作总结"),
        intent_examples=("帮我生成今天的日报", "总结今天做了什么"),
        input_schema={"type": "object", "properties": {}},
        execution={"type": "python_callable", "entrypoint": "daily_agent.skill_handlers.daily_report:run"},
    )

    decision = SkillRouter().route(query="帮我生成今天的日报", candidates=[skill])

    assert decision is not None
    assert decision.skill_name == "daily-report"
    assert decision.route_type == "local"


def test_skill_router_selects_project_inspector_for_project_structure_query():
    skill = SkillRecord(
        name="project-inspector",
        description="检查当前项目结构，梳理关键目录、入口文件和主要流程。",
        status="enabled",
        source="workspace",
        path="/tmp/project-inspector",
        tools=("inspect_project",),
        triggers=("/project", "项目结构", "当前项目", "代码结构"),
        intent_examples=("帮我梳理当前项目结构", "说明聊天和调度两条主流程"),
        input_schema={"type": "object", "properties": {}},
        execution={"type": "python_callable", "entrypoint": "daily_agent.skill_handlers.project_inspector:run"},
    )

    decision = SkillRouter().route(query="帮我梳理当前项目结构，并说明聊天和调度两条主流程", candidates=[skill])

    assert decision is not None
    assert decision.skill_name == "project-inspector"
    assert decision.route_type == "local"


def test_slot_filler_asks_for_required_fields_and_uses_labels():
    skill = SkillRecord(
        name="weather-helper",
        description="查询天气",
        status="enabled",
        source="imported",
        path="/tmp/weather-helper",
        tools=(),
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string", "title": "城市"}},
            "required": ["city"],
        },
        execution={"type": "python_callable", "entrypoint": "pkg.module:run"},
    )

    result = SkillSlotFiller().fill(skill=skill, query="帮我看天气")

    assert result.missing_info == ["city"]
    assert "城市" in build_missing_info_prompt(skill=skill, missing_info=result.missing_info)


def test_slot_filler_extracts_existing_path_for_required_repos(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    skill = SkillRecord(
        name="daily-report",
        description="生成日报",
        status="enabled",
        source="imported",
        path="/tmp/daily-report",
        tools=(),
        input_schema={
            "type": "object",
            "properties": {"repos": {"type": "array", "description": "仓库路径"}},
            "required": ["repos"],
        },
        execution={"type": "python_callable", "entrypoint": "pkg.module:run"},
    )

    result = SkillSlotFiller().fill(skill=skill, query=f"生成日报 {repo}")

    assert result.missing_info == []
    assert result.inputs["repos"] == [str(repo.resolve())]
