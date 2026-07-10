from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dumopro_core.redis_client import RedisClient

from ..deps import get_redis

router = APIRouter()


class ResolveBody(BaseModel):
    station_name: str = Field(min_length=1)
    action: Literal["carry_over", "start_fresh"]


@router.get("/api/stations")
async def list_stations(redis: RedisClient = Depends(get_redis)) -> dict:
    stations = await redis.get_stations()
    pending_names = {c["station_name"] for c in await redis.get_pending_conflicts()}
    now = datetime.now(timezone.utc)
    out = []
    for s in stations:
        name = s["station_name"]
        last_id, cursor_data = await redis.get_cursor(name)
        last_sampled_at = cursor_data.get("last_sampled_at")
        idle_seconds: float | None = None
        if last_sampled_at:
            try:
                dt = datetime.fromisoformat(last_sampled_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                idle_seconds = max(0.0, (now - dt).total_seconds())
            except ValueError:
                pass
        out.append(
            {
                "station_id": s["station_id"],
                "station_name": name,
                "status": s.get("status"),
                "location_info": s.get("location_info"),
                "last_id": last_id,
                "last_sampled_at": last_sampled_at,
                "idle_seconds": idle_seconds,
                "offline": False,
                "pending_conflict": name in pending_names,
            }
        )
    return {"stations": out}


@router.post("/api/stations/sync")
async def trigger_sync(redis: RedisClient = Depends(get_redis)) -> dict:
    """Publish a sync trigger.  The poller's pub/sub listener picks this up
    and runs an immediate reconcile (DB → Redis → tasks)."""
    subscribers = await redis.publish_sync_trigger()
    return {"ok": True, "subscribers": subscribers}


@router.get("/api/stations/conflicts")
async def list_conflicts(redis: RedisClient = Depends(get_redis)) -> dict:
    conflicts = await redis.get_pending_conflicts()
    return {"conflicts": conflicts, "count": len(conflicts)}


@router.post("/api/stations/conflicts/resolve")
async def resolve_conflict(
    body: ResolveBody,
    redis: RedisClient = Depends(get_redis),
) -> dict:
    """Resolve a pending re-registration conflict.

    - `carry_over`: keep all existing Redis data; new task picks up via
      warm-start.  Charts continue from the last frozen candle.
    - `start_fresh`: SCAN+DELETE every key tied to the station_name; new
      task cold-starts (backfills from DB with the new station_id).
    Either way, the conflict marker is cleared and a sync trigger is
    published so the poller starts the new task immediately.
    """
    if not await redis.has_pending_conflict(body.station_name):
        raise HTTPException(status_code=404, detail="no pending conflict")

    # Look up the new_id before clearing the conflict so we can both
    # report it back and mark stations:removed for carry_over (so the
    # next reconcile won't re-trigger the same conflict).
    new_id: str | None = None
    for c in await redis.get_pending_conflicts():
        if c["station_name"] == body.station_name:
            new_id = c["new_id"]
            break

    deleted = 0
    if body.action == "start_fresh":
        deleted = await redis.delete_station_data(body.station_name)
    elif body.action == "carry_over":
        # Acknowledge: poke the removed-marker to the *new* id so the
        # reconciler treats it as already-handled (removed_id == new_id
        # falls through to start_task).
        if new_id:
            await redis.set_removed_station_id(body.station_name, new_id)

    await redis.clear_pending_conflict(body.station_name)
    await redis.publish_sync_trigger()
    return {"ok": True, "action": body.action, "deleted_keys": deleted}
