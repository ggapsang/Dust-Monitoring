# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY libs/gw_proto /tmp/gw_proto
RUN pip install /tmp/gw_proto && rm -rf /tmp/gw_proto

COPY tests/mock_loas_server.py ./mock_loas_server.py

EXPOSE 9001

CMD ["python", "mock_loas_server.py"]
