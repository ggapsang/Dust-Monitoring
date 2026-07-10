from __future__ import annotations

from functools import lru_cache

from fastapi import Request

from dumopro_core.config import Settings, get_settings as _get_settings
from dumopro_core.redis_client import RedisClient


@lru_cache
def get_settings() -> Settings:
    return _get_settings()


def get_redis(request: Request) -> RedisClient:
    return request.app.state.redis


def get_pool(request: Request):
    return request.app.state.pool
