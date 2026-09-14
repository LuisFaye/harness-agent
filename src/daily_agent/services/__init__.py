"""应用服务层统一导出。

这里改成按需懒加载，避免出现：
executor -> services.rendering -> services.__init__ -> agent_service -> executor
这种包级循环导入。
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "AskService",
    "AskServiceResult",
    "ConversationService",
    "DailyReportResult",
    "DailyReportService",
    "DatabaseConnectionService",
    "HealthService",
    "HistoryService",
    "ModelConfigService",
    "McpService",
    "PlanningService",
    "PreferenceService",
    "RunService",
    "RunLifecycleService",
    "ScheduledJobExecutionResult",
    "SchedulerService",
    "SkillCatalogService",
    "ToolService",
    "compute_next_daily_run",
    "format_doctor_text",
    "format_history_text",
    "format_memories_text",
    "parse_schedule_time",
    "redact_config",
]

_EXPORT_MAP = {
    "AskService": ("daily_agent.services.agent_service", "AskService"),
    "AskServiceResult": ("daily_agent.services.agent_service", "AskServiceResult"),
    "ConversationService": ("daily_agent.services.platform_service", "ConversationService"),
    "HealthService": ("daily_agent.services.platform_service", "HealthService"),
    "HistoryService": ("daily_agent.services.platform_service", "HistoryService"),
    "ModelConfigService": ("daily_agent.services.platform_service", "ModelConfigService"),
    "McpService": ("daily_agent.services.capability_service", "McpService"),
    "PlanningService": ("daily_agent.services.platform_service", "PlanningService"),
    "PreferenceService": ("daily_agent.services.platform_service", "PreferenceService"),
    "RunService": ("daily_agent.services.run_service", "RunService"),
    "RunLifecycleService": ("daily_agent.services.run_lifecycle_service", "RunLifecycleService"),
    "SkillCatalogService": ("daily_agent.services.platform_service", "SkillCatalogService"),
    "ToolService": ("daily_agent.services.capability_service", "ToolService"),
    "DailyReportResult": ("daily_agent.services.report_service", "DailyReportResult"),
    "DailyReportService": ("daily_agent.services.report_service", "DailyReportService"),
    "DatabaseConnectionService": ("daily_agent.database_connections", "DatabaseConnectionService"),
    "ScheduledJobExecutionResult": ("daily_agent.services.scheduler_service", "ScheduledJobExecutionResult"),
    "SchedulerService": ("daily_agent.services.scheduler_service", "SchedulerService"),
    "compute_next_daily_run": ("daily_agent.services.scheduler_service", "compute_next_daily_run"),
    "parse_schedule_time": ("daily_agent.services.scheduler_service", "parse_schedule_time"),
    "format_doctor_text": ("daily_agent.services.rendering", "format_doctor_text"),
    "format_history_text": ("daily_agent.services.rendering", "format_history_text"),
    "format_memories_text": ("daily_agent.services.rendering", "format_memories_text"),
    "redact_config": ("daily_agent.services.rendering", "redact_config"),
}


def __getattr__(name: str):
    if name not in _EXPORT_MAP:
        raise AttributeError(name)
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
