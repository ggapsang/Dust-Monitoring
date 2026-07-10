"""Structured logging for PoolerTran (mirrors SocketDaim / decision_agent)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import structlog

# 한국시간(KST, UTC+9) — 로그 timestamp 표기에 사용.
_KST = timezone(timedelta(hours=9))


def _kst_timestamper(_logger, _method, event_dict):
    """로그 timestamp 를 한국시간(KST) ISO8601(+09:00)로 기록."""
    event_dict["timestamp"] = datetime.now(_KST).isoformat()
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _kst_timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if fmt == "console":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=log_level, force=True)
