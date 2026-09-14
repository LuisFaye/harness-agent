"""对话页的运行状态快照，避免高频轮询扫描完整历史和大型 JSON。"""

from datetime import timezone

from fastapi import Depends, FastAPI, HTTPException

from daily_agent.auth_dependencies import require_user
from daily_agent.auth_models import AuthenticatedUser
from daily_agent.errors import DailyAgentError
from daily_agent.memory import MySQLMemoryStore
from daily_agent.run_models import RunListResponse, RunSummary
from daily_agent.services.run_service import sanitize_run_value


def list_run_activity(user_id: str) -> RunListResponse:
    """返回最多 100 条状态，活跃任务优先，其余位置保留最近终态供页面识别完成。"""
    with MySQLMemoryStore()._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_id, user_id, session_id, status, query_text,
                       execution_source, model_id, error_stage, started_at,
                       finished_at, duration_ms, metadata_json IS NOT NULL AS has_detail
                FROM agent_runs
                WHERE user_id = %s
                ORDER BY (status IN ('running', 'queued', 'cancel_requested')) DESC,
                         started_at DESC, run_id DESC
                LIMIT 100
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
    items = []
    for row in rows:
        # 数据库保存 UTC 无时区时间，对外补时区，避免浏览器把 UTC 误认成本地时间。
        started = row['started_at']
        finished = row['finished_at']
        items.append(RunSummary(
            run_id=row['run_id'], user_id=user_id, session_id=row['session_id'],
            status=row['status'],
            query_summary=str(sanitize_run_value(row['query_text'], path='query_summary') or '')[:160],
            response_summary='', execution_source=row['execution_source'] or 'unknown',
            model_id=row['model_id'], error_stage=row['error_stage'],
            started_at=started.replace(tzinfo=timezone.utc) if started else None,
            finished_at=finished.replace(tzinfo=timezone.utc) if finished else None,
            duration_ms=row['duration_ms'], has_detail=bool(row['has_detail']),
        ))
    return RunListResponse(items=items, total=len(items), limit=100, offset=0)


def register_run_activity_route(app: FastAPI) -> None:
    """必须在 /runs/{run_id} 之前注册；身份仅来自登录会话。"""
    @app.get('/runs/activity', response_model=RunListResponse,
             summary='获取当前用户的轻量运行状态快照',
             description='用于对话页状态轮询；最多返回 100 条，活跃任务优先。total 为本次快照条数，不是历史总数。')
    def activity(user: AuthenticatedUser = Depends(require_user)):
        try:
            return list_run_activity(user.id)
        except DailyAgentError as exc:
            raise HTTPException(status_code=503, detail={'code': 'run_store_unavailable', 'message': str(exc)}) from exc
