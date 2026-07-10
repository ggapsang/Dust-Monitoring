from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from dumopro_core.buckets import bucket_score
from dumopro_core.redis_client import RedisClient

from ..deps import get_redis
from ..services.regression import (
    MIN_CANDLES,
    Target,
    run_regression,
)

router = APIRouter()
log = logging.getLogger(__name__)

Unit = Literal["hour", "day", "week", "month"]
Range = Literal["90", "180", "365", "all"]


class RegressionRequest(BaseModel):
    unit: Unit = "day"
    range: Range = "all"
    target: Target = "median"
    extra_targets: list[Target] = Field(default_factory=list)
    degree: int = Field(default=2, ge=1, le=5)
    band_n: float = Field(default=2.0, gt=0)
    percentile: float = Field(default=95.0, gt=0, lt=100)


@router.post("/api/regression/{station}")
async def regression(
    station: str,
    req: RegressionRequest,
    request: Request,
    redis: RedisClient = Depends(get_redis),
) -> dict:
    stations = {s["station_name"] for s in await redis.get_stations()}
    if station not in stations:
        raise HTTPException(status_code=404, detail=f"station {station} not found")

    from datetime import datetime, timedelta, timezone

    if req.range == "all":
        min_score = 0.0
    else:
        days = int(req.range)
        ts = datetime.now(timezone.utc) - timedelta(days=days)
        min_score = bucket_score(ts, req.unit)

    bucket_keys = await redis.get_frozen_range(station, req.unit, min_score, float("inf"))
    candles: list[dict] = []
    for bkey in bucket_keys:
        stats = await redis.get_frozen_stats(station, req.unit, bkey)
        if stats is not None:
            candles.append({"bucket_key": bkey, "stats": stats})
    # include live candle
    pattern_match = f"live:stats:{station}:{req.unit}:"
    live_keys = []
    async for k in redis.raw.scan_iter(match=pattern_match + "*", count=100):
        live_keys.append(k)
    if live_keys:
        live_keys.sort()
        live_bkey = live_keys[-1].split(":")[-1]
        live_stats = await redis.get_live_stats(station, req.unit, live_bkey)
        if live_stats is not None:
            candles.append({"bucket_key": live_bkey, "stats": live_stats})

    if len(candles) < MIN_CANDLES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_candles",
                "message": f"분석 실행에는 최소 {MIN_CANDLES}개의 확정 캔들이 필요합니다. 현재 {len(candles)}개입니다.",
                "required": MIN_CANDLES,
                "have": len(candles),
            },
        )

    primary = await run_regression(
        redis, station, req.unit, candles,
        target=req.target,
        degree=req.degree,
        band_n=req.band_n,
        percentile=req.percentile,
    )
    payload = asdict(primary)
    payload["bucket_keys"] = [c["bucket_key"] for c in candles]

    # Multi-target OR highlighting — run regressions for extra targets and union.
    extras = [t for t in req.extra_targets if t != req.target]
    extra_results = []
    if extras:
        union = set(primary.highlighted_bucket_keys)
        for t in extras:
            r = await run_regression(
                redis, station, req.unit, candles,
                target=t,
                degree=req.degree,
                band_n=req.band_n,
                percentile=req.percentile,
            )
            extra_results.append(
                {
                    "target": t,
                    "rmse": r.rmse,
                    "threshold": r.threshold,
                    "highlighted_bucket_keys": r.highlighted_bucket_keys,
                }
            )
            union.update(r.highlighted_bucket_keys)
        payload["highlighted_bucket_keys"] = sorted(union)
    payload["extra_targets"] = extra_results
    return payload
