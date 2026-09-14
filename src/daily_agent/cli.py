from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from daily_agent.app_context import ApplicationContext
from daily_agent.config import get_config_path, load_config_file, write_default_config
from daily_agent.errors import DailyAgentError
from daily_agent.factories import build_store_factory
from daily_agent.logging_setup import bind_log_context, clear_log_context, log_level_name, setup_logging
from daily_agent.memory import DEFAULT_SESSION_ID, DEFAULT_USER_ID, MySQLMemoryStore
from daily_agent.services import (
    AskService,
    DailyReportService,
    HealthService,
    HistoryService,
    PlanningService,
    PreferenceService,
    SchedulerService,
    SkillCatalogService,
    format_doctor_text,
    format_history_text,
    format_memories_text,
    redact_config,
)

logger = logging.getLogger(__name__)


def _slugify_plan_name(text: str, fallback: str = "plan") -> str:
    """把计划标题整理成适合文件名的短 slug。"""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:40] or fallback


def _default_plan_path(query: str) -> Path:
    """生成默认的计划文件路径。"""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return Path("plans") / f"{timestamp}-{_slugify_plan_name(query)}.md"


def _render_plan_output(plan, query: str, output_format: str) -> str:
    """把计划渲染成 CLI 输出文本。"""
    return PlanningService().render_plan(plan, query, output_format)


def _save_plan_markdown(plan, query: str, output_path: str | None = None, *, app_context: ApplicationContext) -> Path:
    """把计划保存成 Markdown 文件。"""
    if output_path:
        target = Path(output_path).expanduser()
        if not target.is_absolute():
            target = app_context.paths.workspace_root / target
    else:
        target = app_context.paths.data_dir / _default_plan_path(query)
    return PlanningService().save_plan_markdown(plan, query, target)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    app_context = ApplicationContext.build()
    store_factory = build_store_factory(settings=app_context.settings, paths=app_context.paths)
    log_path = setup_logging(process_role="cli", paths=app_context.paths)
    logger.info("CLI 启动：level=%s log_file=%s command=%s", log_level_name(), log_path, getattr(args, "command", None))

    try:
        if args.command == "ask":
            query = _join_text_args(args.query)
            bind_log_context(user_id=args.user_id, session_id=args.session_id, process_role="cli")
            logger.info("开始执行 CLI ask")
            result = AskService(
                settings=app_context.settings,
                store_factory=store_factory,
                working_directory=str(app_context.paths.workspace_root),
            ).run(query=query, user_id=args.user_id, session_id=args.session_id)
            output = result.response_text
            print(output, end="" if output.endswith("\n") else "\n")
            return 0
        if args.command == "memory":
            store_factory().init_schema()
            print("MySQL 记忆库初始化完成。")
            return 0
        if args.command == "config":
            if args.config_command == "init":
                path = write_default_config(path=app_context.paths.config_path, overwrite=args.force)
                print(f"配置文件已准备好：{path}")
                return 0
            if args.config_command == "path":
                print(get_config_path(paths=app_context.paths))
                return 0
            if args.config_command == "show":
                print(json.dumps(redact_config(load_config_file(paths=app_context.paths)), ensure_ascii=False, indent=2))
                return 0
        if args.command == "prefs":
            service = PreferenceService()
            if args.prefs_command == "set":
                service.set_preference(args.key, args.value, user_id=args.user_id)
                print(f"已保存偏好：{args.key} = {args.value}")
                return 0
            if args.prefs_command == "list":
                preferences = service.list_preferences(user_id=args.user_id)
                print(json.dumps([item.__dict__ for item in preferences], ensure_ascii=False, default=str, indent=2))
                return 0
            if args.prefs_command == "delete":
                deleted = service.delete_preference(args.key, user_id=args.user_id)
                print("已删除。" if deleted else "没有找到这个偏好。")
                return 0
        if args.command == "remember":
            text = _join_text_args(args.text)
            store_factory().add_memory(text, user_id=args.user_id)
            _safe_extract_memories_for_remember(text, args.user_id)
            print("已保存记忆。")
            return 0
        if args.command == "memories":
            store = store_factory()
            if args.memories_command == "list":
                rows = store.list_memories(user_id=args.user_id, limit=args.limit)
                if args.format == "json":
                    print(json.dumps([item.__dict__ for item in rows], ensure_ascii=False, default=str, indent=2))
                else:
                    print(format_memories_text(rows))
                return 0
            if args.memories_command == "delete":
                deleted = store.delete_memory(args.id, user_id=args.user_id)
                print("已删除记忆。" if deleted else "没有找到这条记忆。")
                return 0
            if args.memories_command == "dedupe":
                result = store.dedupe_memories(user_id=args.user_id)
                if args.format == "json":
                    print(json.dumps(result.__dict__, ensure_ascii=False, default=str, indent=2))
                else:
                    print(
                        "长期记忆去重完成："
                        f"扫描 {result.scanned_count} 条，删除 {result.deleted_count} 条重复记忆。"
                    )
                return 0
        if args.command == "sessions":
            store = store_factory()
            if args.sessions_command == "cleanup":
                result = store.cleanup_conversation_history(
                    user_id=args.user_id,
                    older_than_days=args.older_than_days,
                    session_id=args.session_id,
                )
                if args.format == "json":
                    print(json.dumps(result.__dict__, ensure_ascii=False, default=str, indent=2))
                else:
                    print(_format_conversation_cleanup_text(result))
                return 0
        if args.command == "history":
            rows = HistoryService().list_history(kind=args.kind, user_id=args.user_id, limit=args.limit)
            if args.format == "text":
                print(format_history_text(rows))
            else:
                print(json.dumps([item.__dict__ for item in rows], ensure_ascii=False, default=str, indent=2))
            return 0
        if args.command == "doctor":
            checks = HealthService(app_context=app_context).run_checks()
            print(format_doctor_text(checks) if args.format == "text" else json.dumps(checks, ensure_ascii=False, indent=2))
            return 0
        if args.command == "plan":
            query = _join_text_args(args.query)
            plan = PlanningService().create_plan(query)
            output = _render_plan_output(plan, query, args.format)
            print(output, end="" if output.endswith("\n") else "\n")
            if args.save or args.output:
                saved_path = _save_plan_markdown(plan, query, args.output, app_context=app_context)
                print(f"计划已保存：{saved_path}", file=sys.stderr)
            return 0
        if args.command == "report":
            # report 是“立刻生成一次日报”的命令，适合手工补日报或先验证扫描结果。
            repos = _split_csv_or_repeat(args.repos)
            extra_notes = _split_csv_or_repeat(args.extra_notes)
            logger.info("开始执行 report：repos=%s date=%s", len(repos), args.date)
            result = DailyReportService().generate(
                target_date=datetime.strptime(args.date, "%Y-%m-%d").date(),
                repos=repos,
                author=args.author,
                extra_notes=extra_notes,
            )
            if args.format == "json":
                print(json.dumps({"report_text": result.report_text, "trace": result.trace}, ensure_ascii=False, indent=2))
            else:
                print(result.report_text)
            return 0
        if args.command == "schedule":
            # schedule 是“管理定时任务定义”，和 report 的区别是：
            # report 立即产出正文；schedule 只把规则存进数据库，等待 worker 到点执行。
            scheduler = SchedulerService()
            if args.schedule_command == "report":
                repos = _split_csv_or_repeat(args.repos)
                extra_notes = _split_csv_or_repeat(args.extra_notes)
                title = args.title or f"工作日报 {args.time}"
                logger.info("创建定时任务：title=%s repos=%s time=%s", title, len(repos), args.time)
                job = scheduler.create_daily_report_job(
                    title=title,
                    schedule_time=args.time,
                    repos=repos,
                    user_id=args.user_id,
                    timezone_name=args.timezone,
                    author=args.author,
                    extra_notes=extra_notes,
                )
                print(
                    f"已创建定时日报任务：#{job.id} {job.title}，"
                    f"每天 {job.schedule_time} 执行，下次运行时间 {job.next_run_at}。"
                )
                return 0
            if args.schedule_command == "list":
                rows = scheduler.list_jobs(user_id=args.user_id, limit=args.limit)
                if args.format == "json":
                    print(json.dumps([item.__dict__ for item in rows], ensure_ascii=False, default=str, indent=2))
                else:
                    print(_format_scheduled_jobs_text(rows))
                return 0
            if args.schedule_command == "runs":
                rows = scheduler.list_job_runs(job_id=args.job_id, limit=args.limit)
                if args.format == "json":
                    print(json.dumps([item.__dict__ for item in rows], ensure_ascii=False, default=str, indent=2))
                else:
                    print(_format_scheduled_job_runs_text(rows))
                return 0
            if args.schedule_command == "pause":
                updated = scheduler.set_job_status(args.job_id, status="paused", user_id=args.user_id)
                print("已暂停任务。" if updated else "没有找到这个任务。")
                return 0
            if args.schedule_command == "resume":
                updated = scheduler.set_job_status(args.job_id, status="enabled", user_id=args.user_id)
                print("已恢复任务。" if updated else "没有找到这个任务。")
                return 0
        if args.command == "logs":
            rows = store_factory().list_tool_call_logs(user_id=args.user_id, limit=args.limit)
            print(json.dumps([item.__dict__ for item in rows], ensure_ascii=False, default=str, indent=2))
            return 0
        if args.command == "skills":
            print(json.dumps(list_skills(), ensure_ascii=False, indent=2))
            return 0
    except (DailyAgentError, ValueError) as exc:
        logger.exception("CLI 执行失败：command=%s", getattr(args, "command", None))
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    finally:
        clear_log_context()
        bind_log_context(process_role="cli")

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sb-agent",
        description="基于 Agno 的 SB Agent。",
    )
    subparsers = parser.add_subparsers(dest="command")

    ask = subparsers.add_parser("ask", help="用自然语言向 Agent 提问。")
    ask.add_argument("query", nargs="+", help="自然语言任务描述，建议使用中文。")
    ask.add_argument("--user-id", default=DEFAULT_USER_ID, help="记忆库用户 ID。")
    ask.add_argument("--session-id", default=DEFAULT_SESSION_ID, help="短期会话 ID，用于串联最近几轮对话。")

    memory = subparsers.add_parser("memory", help="初始化 MySQL 记忆库表结构。")
    memory.add_argument("memory_command", choices=["init"])

    config = subparsers.add_parser("config", help="管理本地 config.toml 配置文件。")
    config_subparsers = config.add_subparsers(dest="config_command")
    config_init = config_subparsers.add_parser("init")
    config_init.add_argument("--force", action="store_true", help="覆盖已存在的 config.toml。")
    config_subparsers.add_parser("path")
    config_subparsers.add_parser("show")

    prefs = subparsers.add_parser("prefs", help="管理 MySQL 记忆库中的用户偏好。")
    prefs_subparsers = prefs.add_subparsers(dest="prefs_command")
    prefs_set = prefs_subparsers.add_parser("set")
    prefs_set.add_argument("key")
    prefs_set.add_argument("value")
    prefs_set.add_argument("--user-id", default=DEFAULT_USER_ID)
    prefs_list = prefs_subparsers.add_parser("list")
    prefs_list.add_argument("--user-id", default=DEFAULT_USER_ID)
    prefs_delete = prefs_subparsers.add_parser("delete")
    prefs_delete.add_argument("key")
    prefs_delete.add_argument("--user-id", default=DEFAULT_USER_ID)

    remember = subparsers.add_parser("remember", help="保存一条手动记忆。")
    remember.add_argument("text", nargs="+")
    remember.add_argument("--user-id", default=DEFAULT_USER_ID)

    memories = subparsers.add_parser("memories", help="查看或删除长期文本记忆。")
    memories_subparsers = memories.add_subparsers(dest="memories_command")
    memories_list = memories_subparsers.add_parser("list")
    memories_list.add_argument("--user-id", default=DEFAULT_USER_ID)
    memories_list.add_argument("--limit", type=int, default=20)
    memories_list.add_argument("--format", choices=["json", "text"], default="text")
    memories_delete = memories_subparsers.add_parser("delete")
    memories_delete.add_argument("id", type=int)
    memories_delete.add_argument("--user-id", default=DEFAULT_USER_ID)
    memories_dedupe = memories_subparsers.add_parser("dedupe", help="清理同一用户下重复的长期记忆。")
    memories_dedupe.add_argument("--user-id", default=DEFAULT_USER_ID)
    memories_dedupe.add_argument("--format", choices=["json", "text"], default="text")

    sessions = subparsers.add_parser("sessions", help="管理短期会话记忆。")
    sessions_subparsers = sessions.add_subparsers(dest="sessions_command")
    sessions_cleanup = sessions_subparsers.add_parser("cleanup", help="清理指定天数以前的短期会话原文和摘要。")
    sessions_cleanup.add_argument("--user-id", default=DEFAULT_USER_ID)
    sessions_cleanup.add_argument("--session-id", help="只清理某个短期会话 ID。")
    sessions_cleanup.add_argument("--older-than-days", type=int, default=30, help="清理多少天以前的会话，默认 30。")
    sessions_cleanup.add_argument("--format", choices=["json", "text"], default="text")

    history = subparsers.add_parser("history", help="查看 MySQL 记忆库中的近期任务历史。")
    history.add_argument("--user-id", default=DEFAULT_USER_ID)
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--kind", choices=["tasks", "tools"], default="tasks")
    history.add_argument("--format", choices=["json", "text"], default="text")

    logs = subparsers.add_parser("logs", help="查看工具调用日志。")
    logs.add_argument("--user-id", default=DEFAULT_USER_ID)
    logs.add_argument("--limit", type=int, default=20)

    doctor = subparsers.add_parser("doctor", help="检查配置、MySQL 和表结构可用性。")
    doctor.add_argument("--format", choices=["json", "text"], default="text")

    plan = subparsers.add_parser("plan", help="只生成规则任务计划，可输出 JSON 或 Markdown。")
    plan.add_argument("query", nargs="+")
    plan.add_argument("--format", choices=["json", "markdown"], default="json", help="输出格式，默认 JSON。")
    plan.add_argument("--save", action="store_true", help="将 Markdown 计划保存到 SB Agent 数据目录的 plans/。")
    plan.add_argument("--output", help="指定保存路径；若不带目录则按当前工作目录解析。")

    report = subparsers.add_parser("report", help="根据 git 提交记录生成日报。")
    report.add_argument("--repos", nargs="+", required=True, help="仓库路径，可传多个，或用逗号分隔。")
    report.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    report.add_argument("--author", help="按作者名或邮箱过滤。")
    report.add_argument("--extra-notes", nargs="*", default=[], help="补充不在提交记录中的工作，可传多个或逗号分隔。")
    report.add_argument("--format", choices=["text", "json"], default="text")

    schedule = subparsers.add_parser("schedule", help="管理定时任务。")
    schedule_subparsers = schedule.add_subparsers(dest="schedule_command")
    schedule_report = schedule_subparsers.add_parser("report", help="创建一个定时工作日报任务。")
    schedule_report.add_argument("--time", required=True, help="每天执行时间，格式 HH:MM，例如 18:00。")
    schedule_report.add_argument("--repos", nargs="+", required=True, help="仓库路径，可传多个，或用逗号分隔。")
    schedule_report.add_argument("--title", help="任务标题，默认自动生成。")
    schedule_report.add_argument("--author", help="按作者名或邮箱过滤。")
    schedule_report.add_argument("--extra-notes", nargs="*", default=[], help="补充不在提交记录中的工作。")
    schedule_report.add_argument("--timezone", default="Asia/Shanghai")
    schedule_report.add_argument("--user-id", default=DEFAULT_USER_ID)

    schedule_list = schedule_subparsers.add_parser("list", help="查看定时任务列表。")
    schedule_list.add_argument("--user-id", default=DEFAULT_USER_ID)
    schedule_list.add_argument("--limit", type=int, default=20)
    schedule_list.add_argument("--format", choices=["text", "json"], default="text")

    schedule_runs = schedule_subparsers.add_parser("runs", help="查看某个定时任务的执行记录。")
    schedule_runs.add_argument("job_id", type=int)
    schedule_runs.add_argument("--limit", type=int, default=20)
    schedule_runs.add_argument("--format", choices=["text", "json"], default="text")

    schedule_pause = schedule_subparsers.add_parser("pause", help="暂停定时任务。")
    schedule_pause.add_argument("job_id", type=int)
    schedule_pause.add_argument("--user-id", default=DEFAULT_USER_ID)

    schedule_resume = schedule_subparsers.add_parser("resume", help="恢复定时任务。")
    schedule_resume.add_argument("job_id", type=int)
    schedule_resume.add_argument("--user-id", default=DEFAULT_USER_ID)

    subparsers.add_parser("skills", help="列出当前已注册的本地技能。")
    return parser


def list_skills() -> list[dict[str, object]]:
    """返回当前已注册的技能清单。

    这里不再只看内置 skill，而是统一读取 skill 注册表，
    这样 CLI、前端工作台和运行时看到的启停状态才一致。
    """
    return SkillCatalogService().list_skills()


def _join_text_args(value: str | list[str]) -> str:
    if isinstance(value, list):
        return " ".join(value)
    return value


def _safe_extract_memories_for_remember(text: str, user_id: str) -> None:
    """remember 命令保存长期记忆后，顺手尝试抽取结构化偏好。"""
    AskService().extract_memories(text, user_id)


def _split_csv_or_repeat(values: list[str] | None) -> list[str]:
    """把 `--repos a b` 和 `--repos a,b` 这两种写法统一展开成列表。"""
    if not values:
        return []
    result: list[str] = []
    for item in values:
        parts = [part.strip() for part in item.split(",")]
        result.extend(part for part in parts if part)
    return result


def _format_scheduled_jobs_text(rows: list) -> str:
    """把任务列表格式化成适合终端阅读的多行文本。"""
    if not rows:
        return "暂无定时任务。"
    lines = []
    for item in rows:
        lines.append(
            f"#{item.id} [{item.status}] {item.title} "
            f"{item.schedule_time} {item.timezone} next={item.next_run_at}"
        )
    return "\n".join(lines)


def _format_scheduled_job_runs_text(rows: list) -> str:
    """把任务运行记录格式化成终端文本，默认只截取前 120 个字符。"""
    if not rows:
        return "暂无执行记录。"
    lines = []
    for item in rows:
        summary = item.output_text or item.error_message or ""
        lines.append(
            f"#{item.id} job={item.job_id} {item.status} "
            f"start={item.started_at} end={item.finished_at} - {summary[:120]}"
        )
    return "\n".join(lines)


def _format_conversation_cleanup_text(result) -> str:
    sessions = "、".join(result.affected_sessions) if result.affected_sessions else "无"
    return (
        "短期会话清理完成："
        f"删除 {result.deleted_messages} 条会话消息，"
        f"删除 {result.deleted_summaries} 条会话摘要，"
        f"影响会话：{sessions}。"
    )


if __name__ == "__main__":
    raise SystemExit(main())
