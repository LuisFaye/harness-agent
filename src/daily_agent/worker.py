"""定时任务 worker 入口。

它不是常驻循环进程，而是一次性完成“扫描到点任务 -> 执行 -> 输出结果”。
因此最适合被 cron、systemd timer 或其他外部调度器按分钟触发。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from daily_agent.app_context import ApplicationContext
from daily_agent.errors import DailyAgentError
from daily_agent.logging_setup import bind_log_context, clear_log_context, log_level_name, setup_logging
from daily_agent.services import SchedulerService

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """执行一次到点任务扫描。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    app_context = ApplicationContext.build()
    log_path = setup_logging(process_role="worker", paths=app_context.paths)
    logger.info("Worker 启动：level=%s log_file=%s limit=%s format=%s", log_level_name(), log_path, args.limit, args.format)

    try:
        service = SchedulerService()
        # worker 只负责“这一轮该跑谁”，具体日报逻辑和数据库记账都在 SchedulerService 里。
        bind_log_context(process_role="worker")
        results = service.run_due_jobs(limit=args.limit)
        logger.info("Worker 扫描完成：jobs=%s", len(results))
        payload = [
            {
                "job_id": item.job.id,
                "title": item.job.title,
                "status": item.run.status,
                "started_at": item.run.started_at.isoformat(sep=" ", timespec="seconds"),
                "finished_at": item.run.finished_at.isoformat(sep=" ", timespec="seconds")
                if item.run.finished_at
                else None,
                "output_text": item.run.output_text,
                "error_message": item.run.error_message,
            }
            for item in results
        ]
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))
        else:
            if not payload:
                logger.info("当前没有到点的定时任务")
                print("当前没有到点的定时任务。")
            else:
                for item in payload:
                    if item["status"] == "success":
                        logger.info("任务执行成功：job_id=%s title=%s", item["job_id"], item["title"])
                        print(f"[success] #{item['job_id']} {item['title']}")
                        print(item["output_text"] or "")
                    else:
                        logger.warning(
                            "任务执行失败：job_id=%s title=%s error=%s",
                            item["job_id"],
                            item["title"],
                            item["error_message"],
                        )
                        print(f"[error] #{item['job_id']} {item['title']} - {item['error_message']}")
        return 0
    except (DailyAgentError, ValueError) as exc:
        logger.exception("Worker 执行失败")
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    finally:
        clear_log_context()
        bind_log_context(process_role="worker")


def build_parser() -> argparse.ArgumentParser:
    """构建 worker 命令行参数。"""
    parser = argparse.ArgumentParser(
        prog="sb-agent-worker",
        description="扫描并执行到点的 SB Agent 定时任务；适合被 cron 周期调用。",
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
