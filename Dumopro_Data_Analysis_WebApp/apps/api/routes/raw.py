from __future__ import annotations

import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from dumopro_core.db import fetch_samples_latest
from dumopro_core.redis_client import RedisClient

from ..deps import get_pool, get_redis

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/api/raw/{station}")
async def get_raw(
    station: str,
    request: Request,
    limit: int = Query(500, ge=10, le=5000),
    redis: RedisClient = Depends(get_redis),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Return the latest N raw samples for a station, chronologically ordered.

    Used by the 초(raw) line-chart view in the detail tab. Data comes directly
    from Postgres (read-only gw_reader), not Redis — Redis only holds aggregated
    boxplot stats after bucket freeze.
    """
    stations_info = await redis.get_stations()
    match = next((s for s in stations_info if s["station_name"] == station), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"station {station} not found")

    settings = request.app.state.settings
    samples = await fetch_samples_latest(
        pool,
        match["station_id"],
        settings.measurement_type,
        limit,
        source=settings.sample_source,
    )
    return {
        "station": station,
        "limit": limit,
        "count": len(samples),
        "samples": [
            {
                "id": s.id,
                "sampled_at": s.sampled_at.isoformat(),
                "value": s.value,
                "unit": s.unit,
            }
            for s in samples
        ],
    }
