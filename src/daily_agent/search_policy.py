"""为联网搜索生成可执行的来源约束，并在调用前补强搜索参数。"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx


_OFFICIAL_DOMAINS = (
    ("openai", "openai.com"),
    ("deepseek", "deepseek.com"),
    ("深度求索", "deepseek.com"),
    ("agno", "docs.agno.com"),
    ("python", "python.org"),
)
_OFFICIAL_DOCUMENT_INDEXES = {
    "docs.agno.com": "https://docs.agno.com/llms.txt",
}
_SEARCH_TOOL_MARKERS = ("web_search", "search_web", "mcp_web_search")


def build_search_policy_instructions(query: str) -> str:
    del query
    return "\n".join([
        "联网搜索证据规则：",
        "- 回答中的事实必须能由本轮搜索结果直接支持；结果没有写的产品、数字、日期、人物或网址不得自行补充。",
        "- 必须区分结果页面的真实来源。第三方转载、博客或聚合页不能表述成官网信息。",
        "- 最终回答应给出来源名称和可用链接；证据不足时明确说证据不足，并使用保守表述。",
        "- 用户要求官方信息时必须先识别官方域名，只采用对应官网结果；OpenAI、DeepSeek、Agno 分别限定 site:openai.com、site:deepseek.com、site:docs.agno.com，找不到时直接说明未找到官方来源。",
        "- 用户要求当前新闻或热度排名时，至少进行两次不同关键词搜索并交叉核对，不能根据单个聚合页断言“最受关注”。",
        "- 用户明确要求两个链接时，必须拿到两个不同的官方 URL 才能结束；单次结果不足时继续更换具体关键词搜索，最终只输出链接和用途。",
    ])


def apply_search_argument_policy(user_query: str, function_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not any(marker in function_name.lower() for marker in _SEARCH_TOOL_MARKERS):
        return arguments
    query = str(arguments.get("query") or "").strip()
    domain = official_domain_for_query(user_query)
    if _asks_for_official_source(user_query) and domain and f"site:{domain}" not in query.lower():
        query = f"{query} site:{domain}".strip()
    if _asks_for_ranked_current_news(user_query):
        arguments["count"] = max(10, int(arguments.get("count") or 0))
        if "独立来源" not in query:
            query = f"{query} 多家独立来源".strip()
    arguments["query"] = query
    return arguments


def filter_search_result_for_policy(user_query: str, function_name: str, result: Any) -> Any:
    """搜索服务无视 site 条件时，丢弃第三方页面，避免模型把它包装成官网信息。

    用户没有明说“官方/官网”，但问题指向已知产品时（例如“agno 的 xx 怎么用”），
    不做硬过滤——通用第三方结果仍有参考价值——只在结果前面补一条官方文档索引，
    否则模型拿不到任何官方 URL，只能猜测文档路径并连续撞 404。
    """
    if not any(marker in function_name.lower() for marker in _SEARCH_TOOL_MARKERS):
        return result
    domain = official_domain_for_query(user_query)
    if not domain:
        return result
    parsed, return_json_text = _parse_search_result(result)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("pages"), list):
        return result
    if not _asks_for_official_source(user_query):
        return _attach_official_document_index(domain, parsed, return_json_text, result)
    pages = [page for page in parsed["pages"] if _page_matches_domain(page, domain)]
    if _asks_for_two_links(user_query):
        existing_urls = {str(page.get("url") or "") for page in pages if isinstance(page, dict)}
        if len(existing_urls) < 2:
            pages.extend(_discover_official_pages(domain, existing_urls))
    filtered = dict(parsed)
    filtered["pages"] = pages
    filtered["official_domain"] = domain
    if _asks_for_two_links(user_query):
        distinct_urls = {str(page.get("url") or "") for page in pages if isinstance(page, dict) and page.get("url")}
        filtered["required_distinct_official_urls"] = 2
        filtered["found_distinct_official_urls"] = len(distinct_urls)
        if len(distinct_urls) < 2:
            filtered["search_more_required"] = "当前官方链接不足两个，请更换具体主题关键词继续搜索。"
    if not pages:
        filtered["verification_error"] = f"本轮搜索没有返回 {domain} 官方页面，禁止使用第三方结果回答官网信息。"
    if return_json_text:
        return json.dumps(filtered, ensure_ascii=False)
    return filtered


def official_search_verification_failed(query: str, outputs: list[str]) -> bool:
    domain = official_domain_for_query(query)
    if not domain or not _asks_for_official_source(query):
        return False
    marker = f"本轮搜索没有返回 {domain} 官方页面"
    marker_found = False
    for output in outputs:
        parsed, _ = _parse_search_result(output)
        if isinstance(parsed, dict) and isinstance(parsed.get("pages"), list) and parsed["pages"]:
            return False
        marker_found = marker_found or marker in str(output or "")
    return marker_found


def official_search_failure_response(query: str) -> str:
    domain = official_domain_for_query(query) or "对应官网"
    subject = "OpenAI" if "openai" in query.lower() else "DeepSeek" if "deepseek" in query.lower() else "该产品"
    return f"本轮搜索没有返回可核验的 {domain} 官方页面，因此暂时不能可靠总结 {subject} 的官网信息。第三方结果不作为官网证据。"


def official_domain_for_query(query: str) -> str | None:
    lowered = str(query or "").lower()
    for marker, domain in _OFFICIAL_DOMAINS:
        if marker in lowered:
            return domain
    return None


def _parse_search_result(result: Any) -> tuple[Any, bool]:
    if isinstance(result, dict):
        return result, False
    if isinstance(result, str):
        try:
            return json.loads(result), True
        except (TypeError, ValueError):
            return result, False
    return result, False


def _page_matches_domain(page: Any, domain: str) -> bool:
    if not isinstance(page, dict):
        return False
    hostname = urlparse(str(page.get("url") or "")).hostname or ""
    lowered = hostname.lower().rstrip(".")
    expected = domain.lower().rstrip(".")
    return lowered == expected or lowered.endswith(f".{expected}")


def _attach_official_document_index(
    domain: str,
    parsed: dict[str, Any],
    return_json_text: bool,
    original: Any,
) -> Any:
    """结果里没有官方页面时，在 JSON 最前面补上官方文档索引。

    必须放在最前面：长工具结果会被证据压缩成前 2000 字符预览，索引排在末尾就
    等于模型看不到，仍然只会靠猜 URL 去读文档。
    """
    if any(_page_matches_domain(page, domain) for page in parsed["pages"]):
        return original
    index_url = _OFFICIAL_DOCUMENT_INDEXES.get(domain)
    if not index_url:
        return original
    search_payload = {key: value for key, value in parsed.items() if key != "official_document_index"}
    hinted = {
        "official_document_index": {
            "url": index_url,
            "domain": domain,
            "reason": f"本轮搜索没有返回 {domain} 官方页面，第三方转载不能当作官网信息。",
            "instruction": (
                f"需要官方细节时先读取 {index_url}，再从索引里挑出真实文档 URL，不要猜测文档路径。"
            ),
        },
        **search_payload,
    }
    return json.dumps(hinted, ensure_ascii=False) if return_json_text else hinted


def _discover_official_pages(domain: str, existing_urls: set[str]) -> list[dict[str, str]]:
    """读取官网公开的 llms.txt 文档索引，补足搜索引擎没有收录的官方入口。"""
    index_url = _OFFICIAL_DOCUMENT_INDEXES.get(domain)
    if not index_url:
        return []
    if index_url in existing_urls:
        return []
    try:
        response = httpx.get(
            index_url,
            headers={"User-Agent": "SB-Agent/1.0"},
            timeout=5,
            follow_redirects=True,
        )
        response.raise_for_status()
        content = response.text[:500_000]
    except Exception:
        return []
    if not content.strip():
        return []
    return [
        {
            "url": index_url,
            "title": "Official documentation index",
            "snippet": "官网公开的完整文档索引，可用于发现所有官方文档页面。",
        }
    ]


def _asks_for_official_source(query: str) -> bool:
    lowered = str(query or "").lower()
    return any(token in lowered for token in ("官方", "官网", "official"))


def _asks_for_ranked_current_news(query: str) -> bool:
    lowered = str(query or "").lower()
    current = any(token in lowered for token in ("今天", "今日", "最新", "当前", "current", "latest"))
    news = any(token in lowered for token in ("新闻", "消息", "动态", "news"))
    ranked = any(token in lowered for token in ("最受关注", "最热门", "头条", "top", "most"))
    return current and news and ranked


def _asks_for_two_links(query: str) -> bool:
    lowered = str(query or "").lower()
    asks_two = any(token in lowered for token in ("两个", "2个", "2 个", "two"))
    return asks_two and any(token in lowered for token in ("链接", "网址", "url", "link"))
