import logging
import json
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from daily_agent.api import create_app
from daily_agent.attachment_service import AttachmentService
from daily_agent.errors import DailyAgentError
from daily_agent.run_models import RunCancelResponse, RunEventListResponse
from daily_agent.services.run_lifecycle_service import RunLifecycleService


class _FailingAskService:
    def run(self, **kwargs):
        raise RuntimeError("模拟后端异常")


class _SuccessAskService:
    def run(self, **kwargs):
        execution = SimpleNamespace(
            run_id=kwargs["run_id"],
            status="success",
            stop_reason="任务完成",
            missing_info=[],
            observations=[],
        )
        plan = SimpleNamespace(to_metadata=lambda: {"intent": "agent_loop", "steps": []})
        return SimpleNamespace(
            execution=execution,
            response_text="ok",
            plan=plan,
            extracted_memories=[],
            trace=[],
            prompt_snapshot=None,
        )


class _DailyAgentErrorAskService:
    def run(self, **kwargs):
        raise DailyAgentError("参数不对", code="bad_request", stage="ask", run_id="ask-err")


class _RecordingAskService(_SuccessAskService):
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return super().run(**kwargs)


def test_attachment_upload_download_and_ask_context(tmp_path):
    ask_service = _RecordingAskService()
    attachment_service = AttachmentService(root=tmp_path / "attachments")
    app = create_app(
        ask_service=ask_service,
        attachment_service=attachment_service,
        run_lifecycle_service=_RunLifecycleServiceStub(),
    )
    client = TestClient(app)

    uploaded = client.post(
        "/attachments",
        data={"user_id": "u1", "session_id": "s1"},
        files={"file": ("facts.txt", "项目代号是星河。".encode(), "text/plain")},
    )

    assert uploaded.status_code == 200
    attachment = uploaded.json()
    assert attachment["parse_status"] == "success"
    listed = client.get("/attachments", params={"user_id": "u1", "session_id": "s1"})
    assert [item["id"] for item in listed.json()["items"]] == [attachment["id"]]
    downloaded = client.get(
        f"/attachments/{attachment['id']}/download",
        params={"user_id": "u1", "session_id": "s1"},
    )
    assert downloaded.content.decode() == "项目代号是星河。"

    response = client.post(
        "/ask",
        json={
            "query": "项目代号是什么？",
            "user_id": "u1",
            "session_id": "s1",
            "attachment_ids": [attachment["id"]],
        },
    )

    assert response.status_code == 200
    assert "项目代号是星河" in ask_service.calls[0]["extra_context_instructions"]


class _RunServiceStub:
    def __init__(self, detail=None):
        self.detail = detail
        self.list_calls = []
        self.detail_calls = []

    def list_runs(self, **kwargs):
        self.list_calls.append(kwargs)
        return {
            "items": [
                {
                    "run_id": "ask-123",
                    "user_id": kwargs["user_id"],
                    "session_id": "s1",
                    "status": "success",
                    "query_summary": "检查项目",
                    "response_summary": "检查完成",
                    "execution_source": "agent_loop",
                    "has_detail": True,
                    "legacy_record": False,
                }
            ],
            "total": 1,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
        }

    def get_run(self, **kwargs):
        self.detail_calls.append(kwargs)
        return self.detail


class _RunLifecycleServiceStub:
    def __init__(self, event_page=None, cancel_response=None):
        self.event_page = event_page
        self.cancel_response = cancel_response
        self.start_calls = []
        self.recorded_events = []
        self.complete_calls = []
        self.fail_calls = []
        self.list_event_calls = []
        self.cancel_calls = []
        self.cancel_notifiers = {}

    def start_run(self, **kwargs):
        self.start_calls.append(kwargs)

    def record_event(self, event):
        self.recorded_events.append(event)

    def complete_run(self, **kwargs):
        self.complete_calls.append(kwargs)
        event = kwargs.get("terminal_event")
        if event is None:
            event = kwargs["terminal_event_factory"]("run_completed", kwargs["result"])
        self.recorded_events.append(event)
        return kwargs["result"], event

    def fail_run(self, **kwargs):
        self.fail_calls.append(kwargs)
        self.recorded_events.append(kwargs["terminal_event"])

    def list_events(self, **kwargs):
        self.list_event_calls.append(kwargs)
        return self.event_page

    def bind_cancel_notifier(self, **kwargs):
        self.cancel_notifiers[kwargs["run_id"]] = kwargs["notifier"]

    def is_cancel_requested(self, *args, **kwargs):
        return False

    def request_cancel(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return self.cancel_response


class _McpReadStub:
    def read_resource(self, server_id, uri):
        return {"server_id": server_id, "uri": uri, "contents": [{"text": "ready"}]}

    def render_prompt(self, server_id, name, arguments):
        return {
            "server_id": server_id,
            "name": name,
            "description": "问候",
            "messages": [{"role": "user", "content": {"type": "text", "text": f"你好，{arguments['name']}"}}],
        }


def test_ask_stream_logs_worker_exception_and_returns_error_event(caplog):
    app = create_app(
        ask_service=_FailingAskService(),
        planning_service=object(),
        history_service=object(),
        preference_service=object(),
        health_service=object(),
        scheduler_service=object(),
        report_service=object(),
        conversation_service=object(),
        skill_catalog_service=object(),
        model_config_service=object(),
        run_lifecycle_service=_RunLifecycleServiceStub(),
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="daily_agent.api"):
        response = client.post(
            "/ask/stream",
            json={"query": "触发异常", "user_id": "u1", "session_id": "s1"},
        )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert [event["type"] for event in events] == ["run_started", "run_failed"]
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[0]["version"] == "2.0"
    assert events[0]["run_id"] == events[1]["run_id"] == response.headers["x-run-id"]
    assert events[1]["payload"]["message"] == "模拟后端异常"
    assert any(
        record.name == "daily_agent.api"
        and record.levelno == logging.ERROR
        and "Ask stream failed: user_id=u1 session_id=s1" in record.getMessage()
        and record.exc_info
        for record in caplog.records
    )


def test_mcp_resource_and_prompt_endpoints_return_real_content_contract():
    app = create_app(mcp_service=_McpReadStub())
    client = TestClient(app)

    resource = client.get("/mcp/servers/mcp-1/resources/read", params={"uri": "echo://status"})
    prompt = client.post(
        "/mcp/servers/mcp-1/prompts/greeting/render",
        json={"arguments": {"name": "小林"}},
    )

    assert resource.status_code == 200
    assert resource.json()["contents"][0]["text"] == "ready"
    assert prompt.status_code == 200
    assert prompt.json()["messages"][0]["content"]["text"] == "你好，小林"


def test_ask_stream_uses_one_run_id_and_v2_event_envelope():
    class StreamingAskService(_SuccessAskService):
        def run(self, **kwargs):
            kwargs["stream_callback"]("response_started", {})
            kwargs["stream_callback"]("response_delta", {"delta": "o"})
            kwargs["stream_callback"]("response_delta", {"delta": "k"})
            result = super().run(**kwargs)
            result.execution.run_id = kwargs["run_id"]
            return result

    app = create_app(
        ask_service=StreamingAskService(),
        planning_service=object(),
        history_service=object(),
        preference_service=object(),
        health_service=object(),
        scheduler_service=object(),
        report_service=object(),
        conversation_service=object(),
        skill_catalog_service=object(),
        model_config_service=object(),
        run_lifecycle_service=_RunLifecycleServiceStub(),
    )

    response = TestClient(app).post(
        "/ask/stream",
        json={"query": "你好", "user_id": "u1", "session_id": "s1"},
    )

    events = [json.loads(line) for line in response.text.splitlines() if line]
    run_id = response.headers["x-run-id"]
    assert [event["type"] for event in events] == [
        "run_started",
        "response_started",
        "response_delta",
        "response_delta",
        "response_completed",
        "run_completed",
    ]
    assert [event["sequence"] for event in events] == list(range(1, 7))
    assert {event["version"] for event in events} == {"2.0"}
    assert {event["run_id"] for event in events} == {run_id}
    assert events[-1]["payload"]["result"]["run_id"] == run_id


def test_ask_logs_request_entry_and_run_id(caplog):
    lifecycle_service = _RunLifecycleServiceStub()
    app = create_app(
        ask_service=_SuccessAskService(),
        planning_service=object(),
        history_service=object(),
        preference_service=object(),
        health_service=object(),
        scheduler_service=object(),
        report_service=object(),
        conversation_service=object(),
        skill_catalog_service=object(),
        model_config_service=object(),
        run_lifecycle_service=lifecycle_service,
    )
    client = TestClient(app)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/ask",
            json={"query": "你好", "user_id": "u1", "session_id": "s1"},
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == lifecycle_service.start_calls[0]["run_id"]
    assert response.json()["run_id"].startswith("ask-")
    assert any("收到 /ask 请求" in record.getMessage() for record in caplog.records)


def test_ask_forwards_client_messages_for_dedupe():
    """回显消息原样交给 AskService：只有那里能同时看到原生历史并去掉重复内容。"""
    ask_service = _RecordingAskService()
    app = create_app(
        ask_service=ask_service,
        planning_service=object(),
        history_service=object(),
        preference_service=object(),
        health_service=object(),
        scheduler_service=object(),
        report_service=object(),
        conversation_service=object(),
        skill_catalog_service=object(),
        model_config_service=object(),
        run_lifecycle_service=_RunLifecycleServiceStub(),
    )
    client = TestClient(app)

    response = client.post(
        "/ask",
        json={
            "query": "A",
            "user_id": "u1",
            "session_id": "s1",
            "client_messages": [
                {"role": "user", "content": "帮我打开百度"},
                {"role": "assistant", "content": "A. 立即执行"},
                {"role": "user", "content": "A"},
            ],
        },
    )

    assert response.status_code == 200
    assert ask_service.calls[0]["client_messages"] == [
        {"role": "user", "content": "帮我打开百度"},
        {"role": "assistant", "content": "A. 立即执行"},
        {"role": "user", "content": "A"},
    ]
    # API 层不再自行渲染，避免与原生历史重复；渲染与去重在 AskService 内完成。
    assert "助手：A. 立即执行" not in ask_service.calls[0]["extra_context_instructions"]


def test_ask_returns_structured_daily_agent_error():
    app = create_app(
        ask_service=_DailyAgentErrorAskService(),
        planning_service=object(),
        history_service=object(),
        preference_service=object(),
        health_service=object(),
        scheduler_service=object(),
        report_service=object(),
        conversation_service=object(),
        skill_catalog_service=object(),
        model_config_service=object(),
        run_lifecycle_service=_RunLifecycleServiceStub(),
    )
    client = TestClient(app)

    response = client.post(
        "/ask",
        json={"query": "你好", "user_id": "u1", "session_id": "s1"},
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error == {
        "code": "bad_request",
        "message": "参数不对",
        "stage": "ask",
        "run_id": error["run_id"],
        "details": {},
        "retriable": False,
    }
    assert error["run_id"].startswith("ask-")
    assert error["run_id"] != "ask-err"


def test_runs_api_returns_page_and_passes_filters():
    run_service = _RunServiceStub()
    app = create_app(run_service=run_service)
    client = TestClient(app)

    response = client.get(
        "/runs",
        params={
            "user_id": "u1",
            "status": "success",
            "session_id": "s1",
            "keyword": "项目",
            "limit": 10,
            "offset": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["run_id"] == "ask-123"
    assert run_service.list_calls == [
        {
            "user_id": "u1",
            "status": "success",
            "session_id": "s1",
            "keyword": "项目",
            "limit": 10,
            "offset": 20,
        }
    ]


def test_runs_api_rejects_unknown_status():
    app = create_app(run_service=_RunServiceStub())
    response = TestClient(app).get("/runs", params={"status": "finished"})

    assert response.status_code == 422


def test_run_detail_api_returns_404_for_wrong_user_or_run_id():
    run_service = _RunServiceStub(detail=None)
    app = create_app(run_service=run_service)
    client = TestClient(app)

    response = client.get("/runs/ask-404", params={"user_id": "u1"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "run_not_found"
    assert run_service.detail_calls == [{"user_id": "u1", "run_id": "ask-404"}]


def test_run_events_api_returns_sequence_page():
    lifecycle_service = _RunLifecycleServiceStub(
        event_page=RunEventListResponse(
            run_id="ask-123",
            events=[
                {
                    "version": "2.0",
                    "run_id": "ask-123",
                    "sequence": 3,
                    "type": "response_delta",
                    "timestamp": "2026-07-15T18:00:00+08:00",
                    "payload": {"delta": "你好"},
                }
            ],
            last_sequence=3,
            has_more=False,
        )
    )
    app = create_app(run_lifecycle_service=lifecycle_service)

    response = TestClient(app).get(
        "/runs/ask-123/events",
        params={"user_id": "u1", "after_sequence": 2, "limit": 10},
    )

    assert response.status_code == 200
    assert response.json()["events"][0]["payload"] == {"delta": "你好"}
    assert lifecycle_service.list_event_calls == [
        {"user_id": "u1", "run_id": "ask-123", "after_sequence": 2, "limit": 10}
    ]


def test_run_events_api_returns_404_for_wrong_user_or_run_id():
    lifecycle_service = _RunLifecycleServiceStub(event_page=None)
    app = create_app(run_lifecycle_service=lifecycle_service)

    response = TestClient(app).get("/runs/ask-404/events", params={"user_id": "u1"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "run_not_found"


def test_cancel_run_api_returns_current_cancel_status():
    lifecycle_service = _RunLifecycleServiceStub(
        cancel_response=RunCancelResponse(
            run_id="ask-123",
            status="cancel_requested",
            accepted=True,
            executor_notified=True,
            message="已提交停止请求，正在等待当前执行步骤结束。",
        )
    )
    app = create_app(run_lifecycle_service=lifecycle_service)

    response = TestClient(app).post(
        "/runs/ask-123/cancel",
        json={"user_id": "u1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"
    assert response.json()["accepted"] is True
    assert lifecycle_service.cancel_calls == [{"run_id": "ask-123", "user_id": "u1"}]


def test_cancel_run_api_hides_wrong_user_as_not_found():
    app = create_app(run_lifecycle_service=_RunLifecycleServiceStub(cancel_response=None))

    response = TestClient(app).post(
        "/runs/ask-404/cancel",
        json={"user_id": "u1"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "run_not_found"


def test_stream_cancel_endpoint_reaches_cancelled_terminal_event():
    class Repository:
        def __init__(self):
            self.run_id = None
            self.status = None
            self.events = []

        def create_run(self, **kwargs):
            self.run_id = kwargs["run_id"]
            self.status = "running"

        def append_event(self, event):
            self.events.append(event)

        def request_cancel(self, **kwargs):
            if kwargs["run_id"] != self.run_id or kwargs["user_id"] != "u1":
                return None, False
            if self.status == "running":
                self.status = "cancel_requested"
                return self.status, True
            return self.status, False

        def finalize_run(self, **kwargs):
            self.status = kwargs["status"]
            self.events.append(kwargs["terminal_event"])

        def get_run(self, **kwargs):
            return None

        def list_events(self, **kwargs):
            return [], False

    started = threading.Event()

    class BlockingAskService(_SuccessAskService):
        def run(self, **kwargs):
            started.set()
            while not kwargs["cancellation_check"]():
                time.sleep(0.01)
            result = super().run(**kwargs)
            result.execution.status = "cancelled"
            result.execution.stop_reason = "用户取消任务"
            result.response_text = "任务已取消。"
            return result

    repository = Repository()
    lifecycle_service = RunLifecycleService(repository=repository)
    app = create_app(
        ask_service=BlockingAskService(),
        run_lifecycle_service=lifecycle_service,
    )
    stream_result = {}

    def run_stream():
        stream_result["response"] = TestClient(app).post(
            "/ask/stream",
            json={"query": "执行一个较慢任务", "user_id": "u1", "session_id": "s1"},
        )

    thread = threading.Thread(target=run_stream)
    thread.start()
    assert started.wait(timeout=2)

    cancel_response = TestClient(app).post(
        f"/runs/{repository.run_id}/cancel",
        json={"user_id": "u1"},
    )
    thread.join(timeout=3)

    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancel_requested"
    assert thread.is_alive() is False
    events = [json.loads(line) for line in stream_result["response"].text.splitlines() if line]
    assert [event["type"] for event in events] == [
        "run_started",
        "run_cancel_requested",
        "run_cancelled",
    ]
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert events[-1]["payload"]["result"]["status"] == "cancelled"
    assert repository.status == "cancelled"
