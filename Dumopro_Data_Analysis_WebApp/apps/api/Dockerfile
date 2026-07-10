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
COPY apps/api /app/apps/api
COPY frontend /app/frontend

EXPOSE 9105

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=12 \
    CMD curl -fsS http://localhost:9105/api/health || exit 1

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "9105"]
