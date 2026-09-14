from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx

from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_handlers import web_fetch
from daily_agent.skill_runtime import SKILL_STATUS_ERROR, SKILL_STATUS_SUCCESS, SkillExecutionContext


def _context(url: str) -> SkillExecutionContext:
    skill = SkillRecord(
        name="web-fetch",
        description="读取网页",
        path=Path("/tmp/web-fetch"),
        source="workspace",
        status="enabled",
        tools=("web_fetch",),
        tool_name="web_fetch",
        execution={"type": "python_callable", "entrypoint": "daily_agent.skill_handlers.web_fetch:run"},
    )
    return SkillExecutionContext(
        query="读取网页",
        user_id="u1",
        session_id="s1",
        skill=skill,
        working_directory="/tmp",
        inputs={"url": url},
    )


def test_web_fetch_extracts_title_text_and_final_url(monkeypatch):
    request = httpx.Request("GET", "https://example.com/final")
    response = httpx.Response(
        200,
        request=request,
        headers={"content-type": "text/html; charset=utf-8"},
        text="<html><title>示例</title><script>ignore()</script><main><h1>结论</h1><p>正文内容</p></main></html>",
    )
    monkeypatch.setattr(web_fetch, "_validate_url", lambda url: None)
    monkeypatch.setattr(web_fetch, "_request", lambda url: response)

    result = web_fetch.run(context=_context("https://example.com/start"))

    assert result.status == SKILL_STATUS_SUCCESS
    assert result.observation["title"] == "示例"
    assert result.observation["final_url"] == "https://example.com/final"
    assert "正文内容" in result.observation["text"]
    assert "ignore" not in result.observation["text"]


def test_web_fetch_accepts_local_and_lan_addresses(monkeypatch):
    requested_urls = []

    def fake_request(url):
        requested_urls.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, headers={"content-type": "text/plain"}, text="本地服务正常")

    monkeypatch.setattr(web_fetch, "_request", fake_request)

    local_result = web_fetch.run(context=_context("http://127.0.0.1:16666/health"))
    lan_result = web_fetch.run(context=_context("http://192.168.5.16:8080/status"))

    assert local_result.status == SKILL_STATUS_SUCCESS
    assert lan_result.status == SKILL_STATUS_SUCCESS
    assert requested_urls == [
        "http://127.0.0.1:16666/health",
        "http://192.168.5.16:8080/status",
    ]


def test_web_fetch_404_points_model_at_documentation_index(monkeypatch):
    request = httpx.Request("GET", "https://docs.example.com/context/chat-history")
    response = httpx.Response(404, request=request, headers={"content-type": "text/html"})

    def fake_request(url):
        response.raise_for_status()

    monkeypatch.setattr(web_fetch, "_validate_url", lambda url: None)
    monkeypatch.setattr(web_fetch, "_request", fake_request)

    result = web_fetch.run(context=_context("https://docs.example.com/context/chat-history"))

    assert result.status == SKILL_STATUS_ERROR
    assert "HTTP 404" in result.response_text
    assert "不要重复请求同一个 URL" in result.response_text
    assert "https://docs.example.com/llms.txt" in result.response_text
    # 不能再把 httpx 的英文原文和 MDN 链接透给模型
    assert "developer.mozilla.org" not in result.response_text
    assert "Client error" not in result.response_text


def test_web_fetch_404_on_non_document_host_skips_index_hint(monkeypatch):
    def fake_request(url):
        response = httpx.Response(404, request=httpx.Request("GET", url))
        response.raise_for_status()

    monkeypatch.setattr(web_fetch, "_validate_url", lambda url: None)
    monkeypatch.setattr(web_fetch, "_request", fake_request)

    result = web_fetch.run(context=_context("https://example.com/missing"))

    assert result.status == SKILL_STATUS_ERROR
    assert "llms.txt" not in result.response_text


def test_web_fetch_server_error_keeps_status_code(monkeypatch):
    def fake_request(url):
        response = httpx.Response(503, request=httpx.Request("GET", url))
        response.raise_for_status()

    monkeypatch.setattr(web_fetch, "_validate_url", lambda url: None)
    monkeypatch.setattr(web_fetch, "_request", fake_request)

    result = web_fetch.run(context=_context("https://docs.example.com/down"))

    assert result.status == SKILL_STATUS_ERROR
    assert "HTTP 503" in result.response_text


def test_web_fetch_reads_real_local_http_service():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = "本机服务读取成功".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = web_fetch.run(context=_context(f"http://127.0.0.1:{server.server_port}/health"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.status == SKILL_STATUS_SUCCESS
    assert result.observation["text"] == "本机服务读取成功"
