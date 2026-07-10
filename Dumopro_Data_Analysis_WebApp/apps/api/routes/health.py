from __future__ import annotations

from fastapi import APIRouter, Depends

from dumopro_core.redis_client import RedisClient

from ..deps import get_redis

router = APIRouter()


@router.get("/api/health")
async def health(redis: RedisClient = Depends(get_redis)) -> dict:
    try:
        ok = await redis.ping()
    except Exception:
        ok = False
    return {"status": "ok" if ok else "degraded", "redis": ok}
