# Build dependencies separately so the runtime image contains no build tools.
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.txt ./requirements.txt
RUN python -m pip install --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8000 \
    WEB_CONCURRENCY=1 \
    DATABASE_PATH=/var/lib/support-agent/support_agent.db \
    LANGGRAPH_STRICT_MSGPACK=true

RUN groupadd --gid 10001 support-agent \
    && useradd --uid 10001 --gid support-agent --create-home --shell /usr/sbin/nologin support-agent \
    && mkdir -p /app /var/lib/support-agent \
    && chown -R support-agent:support-agent /app /var/lib/support-agent
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --chown=support-agent:support-agent src ./src

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:' + __import__('os').getenv('PORT', '8000') + '/health', timeout=2)" || exit 1
CMD ["python", "-m", "support_agent.server"]
