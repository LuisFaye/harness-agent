# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-builder

WORKDIR /build/frontend
RUN npm install --global pnpm@11.9.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SB_AGENT_APP_HOME=/data \
    SB_AGENT_DATA_DIR=/data \
    SB_AGENT_LOG_DIR=/data/logs \
    SB_AGENT_WORKSPACE_ROOT=/workspace \
    SB_AGENT_API_PORT=16666 \
    SB_AGENT_AGNO_TELEMETRY=false

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY sql/ ./sql/
COPY src/ ./src/
COPY --from=frontend-builder /build/src/daily_agent/web_dist/ ./src/daily_agent/web_dist/
COPY docker/entrypoint.sh /usr/local/bin/sb-agent-entrypoint

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 sb-agent \
    && mkdir -p /data /workspace \
    && chown -R sb-agent:sb-agent /data /workspace \
    && chmod 0755 /usr/local/bin/sb-agent-entrypoint

USER sb-agent
WORKDIR /workspace

EXPOSE 16666
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl --fail --silent --show-error http://127.0.0.1:16666/health || exit 1

ENTRYPOINT ["sb-agent-entrypoint"]
CMD ["serve"]
