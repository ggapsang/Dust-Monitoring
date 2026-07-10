# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# -- Python deps -----------------------------------------------------------
# (gw_proto 불필요 — 타깃이 TCP 가 아니라 MariaDB upsert 로 전환됨)
COPY requirements.txt .
RUN pip install -r requirements.txt

# -- gateway source --------------------------------------------------------
COPY egress_gateway/ ./egress_gateway/

# -- outbox dir ------------------------------------------------------------
RUN mkdir -p /data

CMD ["python", "-m", "egress_gateway.main"]
