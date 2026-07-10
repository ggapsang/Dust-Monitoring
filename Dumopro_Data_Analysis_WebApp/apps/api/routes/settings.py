from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from dumopro_core.redis_client import RedisClient

from ..deps import get_redis

router = APIRouter()
log = logging.getLogger(__name__)

# Settings exposed to the UI. Defaults mirror Settings() but are stored in Redis
# at config:runtime as a Hash so both Poller and API can read them.
#
# Scope (per plan §9):
# - Poller settings: require server restart or signal reload (out of scope v0.1).
# - Regression settings: apply on next analysis run.
# - Chart settings: apply immediately.
DEFAULTS: dict[str, Any] = {
    "poll_interval_sec": 1.5,
    "poll_batch_limit": 500,
    "restart_wait_sec": 10.0,
    "consecutive_failure_cap": 5,
    "grace_period_sec": 30.0,
    "regression_degree": 2,
    "regression_band_n": 2.0,
    "regression_percentile": 95.0,
    "regression_default_target": "median",
    "regression_target_combine": "OR",
    "chart_initial_unit": "day",
    "chart_default_ma": "7,30",
    "residual_cap": 10000,
}


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/api/settings")
async def get_settings_route(redis: RedisClient = Depends(get_redis)) -> dict:
    stored = await redis.get_runtime_config()
    merged = dict(DEFAULTS)
    for k, v in stored.items():
        # Runtime values come back as strings; try to coerce to original type.
        if k in DEFAULTS:
            original = DEFAULTS[k]
            try:
                if isinstance(original, bool):
                    merged[k] = v.lower() in ("1", "true", "yes")
                elif isinstance(original, int):
                    merged[k] = int(float(v))
                elif isinstance(original, float):
                    merged[k] = float(v)
                else:
                    merged[k] = v
            except ValueError:
                merged[k] = v
        else:
            merged[k] = v
    return {"defaults": DEFAULTS, "values": merged}


@router.put("/api/settings")
async def put_settings_route(
    req: SettingsUpdate, redis: RedisClient = Depends(get_redis)
) -> dict:
    to_write: dict[str, str] = {}
    for k, v in req.values.items():
        if k not in DEFAULTS:
            continue
        to_write[k] = str(v)
    if to_write:
        await redis.set_runtime_config(to_write)
    return await get_settings_route(redis)
