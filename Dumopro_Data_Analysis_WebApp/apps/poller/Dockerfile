FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/libs

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY libs /app/libs
COPY apps/__init__.py /app/apps/__init__.py
COPY apps/poller /app/apps/poller

EXPOSE 9106

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=12 \
    CMD curl -fsS http://localhost:9106/health || exit 1

CMD ["python", "-m", "apps.poller.main"]
