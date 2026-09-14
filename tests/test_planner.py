from daily_agent.planner import RuleBasedPlanner


def test_planner_returns_generic_steps_for_domain_queries():
    plan = RuleBasedPlanner().plan("帮我查一个外部领域任务")

    assert plan.intent == "general"
    assert plan.risk_level == "low"
    assert plan.missing_info == []
    assert [step.tool for step in plan.steps] == [None, None, None]


def test_planner_does_not_bind_fixed_domain_tools():
    plan = RuleBasedPlanner().plan("看一下今天价格，再给我风险判断")

    assert plan.intent == "general"
    assert all(step.tool is None for step in plan.steps)


def test_planner_marks_empty_query():
    plan = RuleBasedPlanner().plan("   ")

    assert plan.intent == "empty"
    assert all(step.tool is None for step in plan.steps)
