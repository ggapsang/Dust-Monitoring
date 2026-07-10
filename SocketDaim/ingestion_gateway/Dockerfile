# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# -- Install gw_proto shared library first (changes infrequently) ---------
COPY libs/gw_proto /tmp/gw_proto
RUN pip install /tmp/gw_proto && rm -rf /tmp/gw_proto

# -- Install gateway dependencies -----------------------------------------
COPY requirements.txt .
RUN pip install -r requirements.txt

# -- Copy gateway source ---------------------------------------------------
COPY ingestion_gateway/ ./ingestion_gateway/

# /data/storage 는 호스트 bind mount(./storage)로 들어오므로 컨테이너 내부
# 빌드 시점에는 디렉토리를 만들 필요가 없다. 호스트 ./storage/ 폴더가
# 사전에 존재해야 함 (docker-compose가 알아서 마운트).

EXPOSE 9000

CMD ["python", "-m", "ingestion_gateway.main"]
