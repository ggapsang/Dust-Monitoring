# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul

WORKDIR /app

# -- Runtime dependencies (minimal: no gw_proto, no HTTP server) -----------
RUN pip install \
    "asyncpg>=0.30.0" \
    "pydantic-settings>=2.3.0" \
    "structlog>=24.1.0"

# -- Copy cleaner source ---------------------------------------------------
COPY cleaner/ ./cleaner/

# 컨테이너 안 /data/storage는 호스트 ./storage bind mount로 들어옴.
# unlink를 위해 rw 마운트 필요.

CMD ["python", "-m", "cleaner.main"]
