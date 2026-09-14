"""定时任务服务。

这一层把“定时任务是什么、什么时候该跑、跑完后怎么记账”集中起来，
供 CLI、HTTP API 和 worker 共用，避免三处各写一套流程。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from daily_agent.errors import DailyAgentError
from daily_agent.logging_setup import bind_log_context
from daily_agent.memory import DEFAULT_USER_ID, MySQLMemoryStore, ScheduledJob, ScheduledJobRun
from daily_agent.services.report_service import DailyReportResult, DailyReportService
from daily_agent.services.rendering import summarize_text
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyReportJobConfig:
    """日报任务真正关心的业务配置。

    这些字段会序列化进 scheduled_jobs.config_json。
    之所以单独建一个 dataclass，而不是到处直接操作 dict，
    是为了把字段含义固定下来，避免 CLI / API / worker 对同一份配置理解不一致。
    """

    repos: list[str]
    author: str | None
    extra_notes: list[str]
    target_date_offset_days: int


@dataclass(frozen=True)
class ScheduledJobExecutionResult:
    """一次任务执行后的统一返回值。

    - job: 执行的是哪个任务
    - run: 这次执行产生的运行记录
    - report: 如果任务类型会产出日报正文，这里直接带上，便于 API 直接返回
    """

    job: ScheduledJob
    run: ScheduledJobRun
    report: DailyReportResult | None


class SchedulerService:
    """封装定时任务的创建、查询和执行。"""

    def __init__(
        self,
        store: MySQLMemoryStore | None = None,
        report_service: DailyReportService | None = None,
    ) -> None:
        self.store = store or MySQLMemoryStore()
        self.report_service = report_service or DailyReportService()

    def create_daily_report_job(
        self,
        *,
        title: str,
        schedule_time: str,
        repos: list[str],
        user_id: str = DEFAULT_USER_ID,
        timezone_name: str = "Asia/Shanghai",
        author: str | None = None,
        extra_notes: list[str] | None = None,
        target_date_offset_days: int = 0,
        now: datetime | None = None,
    ) -> ScheduledJob:
        """创建“每天某个时间生成日报”的任务。

        这里做两件事：
        1. 把日报相关配置整理成结构化对象并持久化；
        2. 立刻算出 next_run_at，后续 worker 只需要扫到点任务，不必自己理解业务参数。
        """
        self._ensure_scheduler_tables()
        if not repos:
            raise ValueError("日报任务至少需要一个仓库路径。")
        config = DailyReportJobConfig(
            repos=repos,
            author=author,
            extra_notes=extra_notes or [],
            target_date_offset_days=target_date_offset_days,
        )
        next_run_at = compute_next_daily_run(schedule_time, timezone_name, now=now)
        return self.store.add_scheduled_job(
            job_type="daily_report",
            title=title,
            schedule_time=schedule_time,
            timezone_name=timezone_name,
            next_run_at=next_run_at,
            config=config.__dict__,
            user_id=user_id,
        )

    def list_jobs(self, user_id: str = DEFAULT_USER_ID, limit: int = 50) -> list[ScheduledJob]:
        if not self._scheduler_tables_ready():
            return []
        return self.store.list_scheduled_jobs(user_id=user_id, limit=limit)

    def get_job(self, job_id: int, user_id: str = DEFAULT_USER_ID) -> ScheduledJob:
        self._ensure_scheduler_tables()
        job = self.store.get_scheduled_job(job_id=job_id, user_id=user_id)
        if not job:
            raise DailyAgentError(f"没有找到定时任务：{job_id}")
        return job

    def list_job_runs(self, job_id: int, limit: int = 20) -> list[ScheduledJobRun]:
        if not self._scheduler_tables_ready(include_runs=True):
            return []
        return self.store.list_scheduled_job_runs(job_id=job_id, limit=limit)

    def set_job_status(self, job_id: int, status: str, user_id: str = DEFAULT_USER_ID) -> bool:
        self._ensure_scheduler_tables()
        if status not in {"enabled", "paused"}:
            raise ValueError("任务状态只支持 enabled 或 paused。")
        return self.store.set_scheduled_job_status(job_id, status=status, user_id=user_id)

    def run_due_jobs(self, now: datetime | None = None, limit: int = 20) -> list[ScheduledJobExecutionResult]:
        """扫描并执行所有已经到点的任务。

        这里不做常驻循环，只处理“当前这一次扫描”的结果。
        这样同一套逻辑既能被 CLI 手动触发，也能被 cron / systemd timer 周期调用。
        """
        if not self._scheduler_tables_ready(include_runs=True):
            return []
        current = now or datetime.now()
        jobs = self.store.list_due_scheduled_jobs(now=current, limit=limit)
        logger.info("scheduler scanned due jobs: count=%s limit=%s", len(jobs), limit)
        results: list[ScheduledJobExecutionResult] = []
        for job in jobs:
            results.append(self.run_job(job, now=current))
        return results

    def run_job_now(self, job_id: int, user_id: str = DEFAULT_USER_ID, now: datetime | None = None) -> ScheduledJobExecutionResult:
        """手工立即执行某个任务。

        这个入口主要给前端“立即执行”按钮和调试场景使用。
        它不会要求任务已经到点，只要任务存在就直接跑。
        """
        job = self.get_job(job_id=job_id, user_id=user_id)
        return self.run_job(job, now=now)

    def run_job(self, job: ScheduledJob, now: datetime | None = None) -> ScheduledJobExecutionResult:
        """执行单个任务，并把结果写回任务表、运行记录表、任务历史表。"""
        current = now or datetime.now()
        if job.job_type != "daily_report":
            raise DailyAgentError(f"暂不支持的定时任务类型：{job.job_type}")

        started_at = current
        report_result: DailyReportResult | None = None
        bind_log_context(user_id=job.user_id, session_id=f"schedule-{job.id}")
        logger.info("worker job started: job_id=%s title=%s job_type=%s", job.id, job.title, job.job_type)
        try:
            report_result = self._run_daily_report_job(job, now=current)
            # 即使刚刚执行成功，也要重新计算下一次时间。
            # 这里故意把 now 往后推 1 秒，避免“刚执行完仍然落在同一分钟”时再次算回今天，
            # 导致 worker 下一轮扫描又把同一任务捞出来。
            next_run_at = compute_next_daily_run(
                job.schedule_time,
                job.timezone,
                now=current + timedelta(seconds=1),
            )
            self.store.update_scheduled_job_runtime(job.id, next_run_at=next_run_at, last_run_at=current)
            run = self.store.add_scheduled_job_run(
                job_id=job.id,
                user_id=job.user_id,
                status="success",
                started_at=started_at,
                finished_at=datetime.now(),
                output_text=report_result.report_text,
                metadata=report_result.trace,
            )
            self.store.add_task_history(
                task_type="scheduled_daily_report",
                user_input=f"定时任务#{job.id} {job.title}",
                output_summary=summarize_text(report_result.report_text),
                status="success",
                metadata={"job_id": job.id, "job_type": job.job_type, "trace": report_result.trace},
                user_id=job.user_id,
            )
            logger.info("worker job finished: job_id=%s status=success", job.id)
            return ScheduledJobExecutionResult(job=job, run=run, report=report_result)
        except Exception as exc:
            # 失败也照样推进 next_run_at。
            # 当前实现选择“记录失败，然后等待下一次计划时间重试”，
            # 而不是把任务卡死在当前时间点不断重复执行。
            next_run_at = compute_next_daily_run(
                job.schedule_time,
                job.timezone,
                now=current + timedelta(seconds=1),
            )
            self.store.update_scheduled_job_runtime(job.id, next_run_at=next_run_at, last_run_at=current)
            run = self.store.add_scheduled_job_run(
                job_id=job.id,
                user_id=job.user_id,
                status="error",
                started_at=started_at,
                finished_at=datetime.now(),
                output_text=None,
                error_message=str(exc),
                metadata={"job_id": job.id, "job_type": job.job_type},
            )
            self.store.add_task_history(
                task_type="scheduled_daily_report",
                user_input=f"定时任务#{job.id} {job.title}",
                output_summary=None,
                status="error",
                error_message=str(exc),
                metadata={"job_id": job.id, "job_type": job.job_type},
                user_id=job.user_id,
            )
            logger.exception("worker job failed: job_id=%s", job.id)
            return ScheduledJobExecutionResult(job=job, run=run, report=report_result)

    def _run_daily_report_job(self, job: ScheduledJob, now: datetime) -> DailyReportResult:
        """把任务配置翻译成日报服务可执行的参数。"""
        config = self._parse_daily_report_config(job.config_json)
        target_date = (now.date() + timedelta(days=config.target_date_offset_days))
        return self.report_service.generate(
            target_date=target_date,
            repos=config.repos,
            author=config.author,
            extra_notes=config.extra_notes,
        )

    def _parse_daily_report_config(self, config_json: str | None) -> DailyReportJobConfig:
        """从数据库里的 JSON 配置恢复出强类型配置对象。"""
        raw = json.loads(config_json or "{}")
        repos = [str(item) for item in raw.get("repos", []) if str(item).strip()]
        if not repos:
            raise DailyAgentError("日报任务缺少 repos 配置。")
        author = raw.get("author")
        extra_notes = [str(item) for item in raw.get("extra_notes", [])]
        target_date_offset_days = int(raw.get("target_date_offset_days", 0))
        return DailyReportJobConfig(
            repos=repos,
            author=author,
            extra_notes=extra_notes,
            target_date_offset_days=target_date_offset_days,
        )

    def _scheduler_tables_ready(self, include_runs: bool = False) -> bool:
        """检查定时任务依赖的表是否都已经存在。"""
        if not hasattr(self.store, "table_exists"):
            # 测试替身和极简 store 可能没有表存在性检查能力。
            # 这类对象通常是内存假实现，默认视为已就绪。
            return True
        required_tables = ["scheduled_jobs"]
        if include_runs:
            required_tables.append("scheduled_job_runs")
        try:
            return all(self.store.table_exists(table_name) for table_name in required_tables)
        except DailyAgentError:
            return False

    def _ensure_scheduler_tables(self) -> None:
        """在写操作前确认定时任务相关表已初始化。"""
        if not self._scheduler_tables_ready(include_runs=True):
            raise DailyAgentError("定时任务表尚未初始化，请先运行 sb-agent memory init。")


def compute_next_daily_run(schedule_time: str, timezone_name: str, now: datetime | None = None) -> datetime:
    """根据本地时区和 HH:MM 计算下一次运行时间。

    这里先在目标时区里算“今天/明天几点”，最后再去掉 tzinfo 返回 naive datetime，
    原因是当前 MySQL 表使用的是普通 DATETIME 字段，不带时区信息。
    换句话说：时区只参与“计算”，真正落库时保存的是该时区下的本地时间。
    """
    tz = ZoneInfo(timezone_name)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        # 调用方很多时候传进来的是 naive datetime，例如 worker 的 datetime.now()。
        # 这里按任务自己的 timezone 来解释它，保证“18:00”永远相对于任务时区计算。
        current = current.replace(tzinfo=tz)
    else:
        current = current.astimezone(tz)

    hour, minute = parse_schedule_time(schedule_time)
    target = datetime.combine(current.date(), time(hour=hour, minute=minute), tzinfo=tz)
    if target <= current:
        target += timedelta(days=1)
    return target.replace(tzinfo=None)


def parse_schedule_time(value: str) -> tuple[int, int]:
    """解析 HH:MM 格式的每日执行时间。"""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("时间格式必须是 HH:MM，例如 18:00。")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("时间超出范围，小时应为 0-23，分钟应为 0-59。")
    return hour, minute
