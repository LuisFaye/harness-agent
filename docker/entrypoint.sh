#!/bin/sh
set -eu

if [ "${1:-serve}" != "serve" ]; then
  exec "$@"
fi

attempt=1
max_attempts="${SB_AGENT_DB_INIT_RETRIES:-30}"
until sb-agent memory init; do
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "数据库初始化失败，已达到最大重试次数。" >&2
    exit 1
  fi
  echo "数据库尚未就绪，2 秒后重试（${attempt}/${max_attempts}）。" >&2
  attempt=$((attempt + 1))
  sleep 2
done

exec uvicorn daily_agent.api:app \
  --host 0.0.0.0 \
  --port "${SB_AGENT_API_PORT:-16666}"
