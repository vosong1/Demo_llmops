# Image cho dashboard (chỉ dashboard; Ollama, LiteLLM, Phoenix chạy ở container riêng, xem docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8765 \
    DATA_DIR=/data \
    MANAGE_SERVICES=0 \
    NO_BROWSER=1

# Chạy bằng người dùng thường; /data là nơi lưu users.json, chats/, logs/, docs/, models.json, limits.json
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app /data \
    && chown app:app /app /data
WORKDIR /app

COPY requirements-docker.txt .
RUN pip install -r requirements-docker.txt

COPY --chown=app:app . .
USER app

VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('DASHBOARD_PORT', '8765'), timeout=3)" || exit 1

CMD ["python", "dashboard.py", "--no-browser"]
