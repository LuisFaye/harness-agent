from daily_agent.services.loop_trace import loop_event_to_trace_event


def test_iteration_observed_summary_does_not_expose_raw_tool_output():
    raw_result = '{"pages":[{"snippet":"一段很长的搜索结果"}]}'

    event = loop_event_to_trace_event(
        {
            "event": "iteration_observed",
            "iteration": 1,
            "status": "success",
            "tool": "mcp_web_search",
            "result": raw_result,
        }
    )

    assert event is not None
    assert event.summary == "工具结果已返回，正在判断下一步。"
    assert event.details["result"] == raw_result
