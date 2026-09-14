import io
import json
import logging
from contextlib import redirect_stderr, redirect_stdout

from daily_agent import cli, worker


def test_cli_failure_logs_exception_and_prints_stderr(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv("SB_AGENT_LOG_DIR", str(tmp_path / "logs"))

    class FailingAskService:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            raise ValueError("模拟 CLI 失败")

    monkeypatch.setattr(cli, "AskService", FailingAskService)
    stdout = io.StringIO()
    stderr = io.StringIO()

    with caplog.at_level(logging.ERROR):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = cli.main(["ask", "你好"])

    assert exit_code == 2
    assert "错误：模拟 CLI 失败" in stderr.getvalue()
    assert any("CLI 执行失败" in record.getMessage() for record in caplog.records)


def test_worker_logs_success_and_failure(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv("SB_AGENT_LOG_DIR", str(tmp_path / "logs"))

    class FakeRun:
        def __init__(self, status, output_text=None, error_message=None):
            self.status = status
            self.started_at = _FakeDatetime()
            self.finished_at = _FakeDatetime()
            self.output_text = output_text
            self.error_message = error_message

    class FakeJob:
        def __init__(self, job_id, title):
            self.id = job_id
            self.title = title

    class FakeResult:
        def __init__(self, job_id, title, status, output_text=None, error_message=None):
            self.job = FakeJob(job_id, title)
            self.run = FakeRun(status, output_text=output_text, error_message=error_message)

    class FakeSchedulerService:
        def run_due_jobs(self, limit):
            return [
                FakeResult(1, "日报 A", "success", output_text="ok"),
                FakeResult(2, "日报 B", "error", error_message="boom"),
            ]

    monkeypatch.setattr(worker, "SchedulerService", lambda: FakeSchedulerService())
    stdout = io.StringIO()
    stderr = io.StringIO()

    with caplog.at_level(logging.INFO):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = worker.main([])

    assert exit_code == 0
    text = stdout.getvalue()
    assert "[success] #1 日报 A" in text
    assert "[error] #2 日报 B - boom" in text
    assert any("任务执行成功：job_id=1 title=日报 A" in record.getMessage() for record in caplog.records)
    assert any("任务执行失败：job_id=2 title=日报 B error=boom" in record.getMessage() for record in caplog.records)


class _FakeDatetime:
    def isoformat(self, sep=" ", timespec="seconds"):
        return "2026-06-01 10:00:00"
