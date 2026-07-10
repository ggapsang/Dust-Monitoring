from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from dumopro_core.buckets import bucket_score
from dumopro_core.redis_client import RedisClient

from ..deps import get_redis

router = APIRouter()

Unit = Literal["hour", "day", "week", "month"]
Range = Literal["90", "180", "365", "all"]


def _range_to_min_score(range_: Range, unit: Unit) -> float:
    if range_ == "all":
        return 0.0
    days = int(range_)
    ts = datetime.now(timezone.utc) - timedelta(days=days)
    return bucket_score(ts, unit)


@router.get("/api/chart/{station}")
async def get_chart(
    station: str,
    unit: Unit = Query("day"),
    range: Range = Query("all"),
    redis: RedisClient = Depends(get_redis),
) -> dict:
    stations = {s["station_name"] for s in await redis.get_stations()}
    if station not in stations:
        raise HTTPException(status_code=404, detail=f"station {station} not found")

    min_score = _range_to_min_score(range, unit)
    bucket_keys = await redis.get_frozen_range(station, unit, min_score, float("inf"))

    frozen: list[dict] = []
    for bkey in bucket_keys:
        stats = await redis.get_frozen_stats(station, unit, bkey)
        if stats is not None:
            frozen.append({"bucket_key": bkey, "stats": stats})

    live_bkey, live_stats = await _get_live(redis, station, unit)
    _, cursor_data = await redis.get_cursor(station)

    return {
        "station": station,
        "unit": unit,
        "range": range,
        "frozen": frozen,
        "live": (
            {"bucket_key": live_bkey, "stats": live_stats}
            if live_bkey and live_stats
            else None
        ),
        "last_sampled_at": cursor_data.get("last_sampled_at"),
    }


async def _get_live(redis: RedisClient, station: str, unit: Unit):
    # Find the live bucket by scanning the live:stats:* key for this station+unit.
    # The Poller writes exactly one live bucket per (station, unit) — but during
    # boundary transitions there may briefly be two. We pick the lexicographically
    # greatest bucket_key since day/week/month bucket keys sort chronologically.
    pattern = f"live:stats:{station}:{unit}:*"
    keys = []
    async for k in redis.raw.scan_iter(match=pattern, count=100):
        keys.append(k)
    if not keys:
        return None, None
    keys.sort()
    chosen = keys[-1]
    bkey = chosen.split(":")[-1]
    stats = await redis.get_live_stats(station, unit, bkey)
    return bkey, stats
