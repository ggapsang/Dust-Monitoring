# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# -- Install dependencies --------------------------------------------------
COPY requirements.txt .
RUN pip install -r requirements.txt

# -- Copy admin_ui source --------------------------------------------------
COPY admin_ui/ ./admin_ui/

EXPOSE 9108

CMD ["python", "-m", "admin_ui.main"]
