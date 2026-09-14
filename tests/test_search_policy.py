import daily_agent.search_policy as search_policy
from daily_agent.search_policy import (
    apply_search_argument_policy,
    build_search_policy_instructions,
    filter_search_result_for_policy,
    official_search_verification_failed,
)


def test_official_search_uses_known_official_domain():
    arguments = apply_search_argument_policy(
        "请搜索 DeepSeek 官方模型说明",
        "web_search",
        {"query": "DeepSeek 模型说明"},
    )

    assert arguments["query"] == "DeepSeek 模型说明 site:deepseek.com"


def test_current_ranked_news_requires_broader_search_and_cross_check():
    query = "搜索今天最受关注的 AI 新闻"
    arguments = apply_search_argument_policy(query, "web_search", {"query": "AI 新闻", "count": 3})
    instructions = build_search_policy_instructions(query)

    assert arguments["count"] == 10
    assert "多家独立来源" in arguments["query"]
    assert "至少进行两次不同关键词搜索" in instructions


def test_search_instructions_forbid_calling_third_party_results_official():
    instructions = build_search_policy_instructions("搜索 OpenAI 官方最新模型说明")

    assert "第三方转载、博客或聚合页不能表述成官网信息" in instructions
    assert "site:openai.com" in instructions


def test_official_search_result_drops_third_party_pages():
    raw = {
        "pages": [
            {"url": "https://baike.baidu.com/openai", "title": "百科"},
            {"url": "https://openai.com/news/", "title": "News"},
        ]
    }

    filtered = filter_search_result_for_policy("搜索 OpenAI 官方信息", "web_search", raw)

    assert filtered["pages"] == [{"url": "https://openai.com/news/", "title": "News"}]
    assert "verification_error" not in filtered


def test_official_search_without_official_page_is_marked_unverified():
    raw = '{"pages":[{"url":"https://baike.baidu.com/deepseek"}]}'

    filtered = filter_search_result_for_policy("搜索 DeepSeek 官方信息", "web_search", raw)

    assert official_search_verification_failed("搜索 DeepSeek 官方信息", [filtered])
    assert "禁止使用第三方结果" in filtered


def test_one_verified_official_search_overrides_other_empty_attempts():
    empty = '{"pages":[],"verification_error":"本轮搜索没有返回 docs.agno.com 官方页面，禁止使用第三方结果回答官网信息。"}'
    verified = '{"pages":[{"url":"https://docs.agno.com/introduction"}]}'

    assert not official_search_verification_failed("搜索 Agno 官方文档", [empty, verified])


def test_known_product_query_without_official_wording_gets_index_hint():
    raw = '{"pages":[{"url":"https://blog.csdn.net/x/article/1","title":"博客"}]}'

    filtered = filter_search_result_for_policy(
        "agno的上下文，add_history_to_context，有什么好处和局限？",
        "mcp_web_search_bailian_web_search",
        raw,
    )

    assert filtered.startswith('{"official_document_index":')
    assert "https://docs.agno.com/llms.txt" in filtered
    # 第三方结果仍然保留，只是补了官方索引入口
    assert "blog.csdn.net" in filtered
    assert "verification_error" not in filtered


def test_index_hint_is_skipped_when_official_page_already_present():
    raw = '{"pages":[{"url":"https://docs.agno.com/agents/context","title":"Context"}]}'

    filtered = filter_search_result_for_policy("agno 怎么配置上下文", "web_search", raw)

    assert filtered == raw


def test_index_hint_never_replaces_official_source_filtering():
    raw = '{"pages":[{"url":"https://blog.csdn.net/x/article/1"}]}'

    filtered = filter_search_result_for_policy("搜索 Agno 官方文档", "web_search", raw)

    assert "official_document_index" not in filtered
    assert "verification_error" in filtered


def test_two_link_request_marks_single_official_result_as_incomplete(monkeypatch):
    monkeypatch.setattr(search_policy, "_discover_official_pages", lambda domain, existing_urls: [])
    raw = {"pages": [{"url": "https://docs.agno.com/", "title": "Agno"}]}

    filtered = filter_search_result_for_policy("搜索 Agno 官方文档，给我两个链接", "web_search", raw)
    instructions = build_search_policy_instructions("搜索 Agno 官方文档，给我两个链接")

    assert filtered["found_distinct_official_urls"] == 1
    assert "更换具体主题关键词继续搜索" in filtered["search_more_required"]
    assert "两个不同的官方 URL" in instructions
