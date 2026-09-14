from daily_agent.loop_control import (
    AgentLoopControl,
    REPEATED_CALL_BLOCKED,
    TOOL_LIMIT_BLOCKED,
)


def test_loop_control_blocks_same_tool_and_arguments_before_second_execution():
    calls = []
    control = AgentLoopControl(max_tool_calls=4)

    def tool(**arguments):
        calls.append(arguments)
        return "真实结果"

    first = control.tool_hook("search_web", tool, {"query": "成都天气"})
    second = control.tool_hook("search_web", tool, {"query": "成都天气"})

    assert first == "真实结果"
    assert REPEATED_CALL_BLOCKED in second
    assert calls == [{"query": "成都天气"}]
    assert control.consume_block_reason("search_web", {"query": "成都天气"}) == "检测到相同工具和参数的重复调用，已在执行前拦截"
    assert control.consume_block_reason("search_web", {"query": "成都天气"}) is None


def test_loop_control_blocks_calls_beyond_total_limit():
    control = AgentLoopControl(max_tool_calls=1)

    assert control.tool_hook("first", lambda **_: "一", {}) == "一"
    blocked = control.tool_hook("second", lambda **_: "二", {})

    assert TOOL_LIMIT_BLOCKED in blocked


def test_blocked_duplicate_calls_do_not_consume_tool_budget():
    calls = []
    control = AgentLoopControl(max_tool_calls=3)

    def tool(**arguments):
        calls.append(arguments)
        return "真实结果"

    assert control.tool_hook("web_fetch", tool, {"url": "https://example.com/a"}) == "真实结果"
    # 重复请求同一个失败 URL 被反复拦截，但不吃掉 max_tool_calls 额度
    for _ in range(3):
        assert REPEATED_CALL_BLOCKED in control.tool_hook("web_fetch", tool, {"url": "https://example.com/a"})
    assert control.tool_call_count == 1

    assert control.tool_hook("web_fetch", tool, {"url": "https://example.com/b"}) == "真实结果"
    assert control.tool_hook("web_fetch", tool, {"url": "https://example.com/c"}) == "真实结果"
    assert control.tool_call_count == 3
    assert TOOL_LIMIT_BLOCKED in control.tool_hook("web_fetch", tool, {"url": "https://example.com/d"})
    assert calls == [
        {"url": "https://example.com/a"},
        {"url": "https://example.com/b"},
        {"url": "https://example.com/c"},
    ]


def test_repeated_blocked_calls_still_have_their_own_cap():
    control = AgentLoopControl(max_tool_calls=2)

    assert control.max_blocked_calls == 4

    def tool(**arguments):
        return "结果"

    assert control.tool_hook("web_fetch", tool, {"url": "https://example.com/a"}) == "结果"
    for _ in range(4):
        assert REPEATED_CALL_BLOCKED in control.tool_hook("web_fetch", tool, {"url": "https://example.com/a"})
    # 同一签名反复触发后，守卫用独立的拦截上限兜底，避免无限刷重复调用
    assert TOOL_LIMIT_BLOCKED in control.tool_hook("web_fetch", tool, {"url": "https://example.com/a"})


def test_loop_control_limits_official_search_to_real_official_domain():
    calls = []
    control = AgentLoopControl(user_query="搜索 OpenAI 官方最新模型说明")

    control.tool_hook("web_search", lambda **arguments: calls.append(arguments) or "结果", {"query": "OpenAI 最新模型"})

    assert calls == [{"query": "OpenAI 最新模型 site:openai.com"}]


def test_loop_control_disambiguates_tokyo_as_japan():
    calls = []
    control = AgentLoopControl(user_query="查东京未来两天天气")

    control.tool_hook("weather_query", lambda **arguments: calls.append(arguments) or "结果", {"city": "东京"})

    assert calls == [{"city": "Tokyo, Japan"}]


def test_loop_control_disambiguates_tokyo_from_context_tool_argument():
    calls = []
    control = AgentLoopControl(user_query="查一下目的地未来三天天气")

    control.tool_hook("weather_query", lambda **arguments: calls.append(arguments) or "结果", {"city": "东京"})

    assert calls == [{"city": "Tokyo, Japan"}]


def test_loop_control_blocks_extraterrestrial_weather_fallback_to_earth_namesake():
    calls = []
    control = AgentLoopControl(user_query="查询火星奥林匹斯山天气")

    control.tool_hook("weather_query", lambda **arguments: calls.append(arguments) or "找不到", {"city": "火星奥林匹斯山"})
    blocked = control.tool_hook("weather_query", lambda **arguments: calls.append(arguments) or "错误地点", {"city": "火星"})

    assert REPEATED_CALL_BLOCKED in blocked
    assert calls == [{"city": "火星奥林匹斯山"}]
