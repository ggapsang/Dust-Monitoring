from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg
import numpy as np

from dumopro_core.buckets import UNIT_LIST, bucket_key, bucket_score
from dumopro_core.candles import compute_box_stats
from dumopro_core.db import iter_all_samples
from dumopro_core.keys import Unit
from dumopro_core.models import StationInfo
from dumopro_core.redis_client import RedisClient
from dumopro_core.serialize import candle_to_json

log = logging.getLogger(__name__)


async def cold_start(
    station: StationInfo,
    pool: asyncpg.Pool,
    redis: RedisClient,
    measurement_type: str,
    *,
    sample_source: str = "v_loas_sensor_sample",
) -> tuple[int, datetime | None]:
    """Stream all samples for a station, build frozen candles per unit,
    keep only the final bucket as live.

    Returns (last_id, last_sampled_at).
    """
    buffers: dict[Unit, dict[str, list[tuple[int, float]]]] = {u: {} for u in UNIT_LIST}
    last_id = 0
    last_sampled_at: datetime | None = None
    sample_count = 0

    async for s in iter_all_samples(
        pool, station.station_id, measurement_type, source=sample_source,
    ):
        last_id = s.id
        last_sampled_at = s.sampled_at
        sample_count += 1
        for unit in UNIT_LIST:
            bkey = bucket_key(s.sampled_at, unit)
            buffers[unit].setdefault(bkey, []).append((s.id, s.value))

    if sample_count == 0:
        log.info("cold_start.empty station=%s", station.station_name)
        return 0, None

    # For each unit: freeze all buckets except the most recent (kept as live).
    for unit in UNIT_LIST:
        per_unit = buffers[unit]
        if not per_unit:
            continue
        sorted_bkeys = sorted(per_unit.keys())
        live_bkey = sorted_bkeys[-1]
        for bkey in sorted_bkeys[:-1]:
            items = per_unit[bkey]
            values = np.array([v for _, v in items], dtype=np.float64)
            stats = compute_box_stats(values)
            # Use first sample's timestamp in the bucket for scoring (start-of-bucket)
            any_ts = _infer_ts_for_bucket(bkey, unit)
            score = bucket_score(any_ts, unit)
            await redis.freeze_bucket(
                station.station_name,
                unit,
                bkey,
                candle_to_json(stats, updated_at=last_sampled_at),
                score,
            )
        # Live bucket: raw z-adds + stats
        live_items = per_unit[live_bkey]
        for sid, val in live_items:
            await redis.add_live_raw(station.station_name, unit, live_bkey, sid, val)
        live_values = np.array([v for _, v in live_items], dtype=np.float64)
        live_stats = compute_box_stats(live_values)
        await redis.set_live_stats(
            station.station_name,
            unit,
            live_bkey,
            candle_to_json(live_stats, updated_at=last_sampled_at),
        )
        log.info(
            "cold_start.unit station=%s unit=%s frozen=%d live_bucket=%s samples=%d",
            station.station_name,
            unit,
            len(sorted_bkeys) - 1,
            live_bkey,
            len(live_items),
        )

    await redis.set_cursor(
        station.station_name, last_id=last_id, last_sampled_at=last_sampled_at
    )
    log.info(
        "cold_start.done station=%s samples=%d last_id=%d",
        station.station_name,
        sample_count,
        last_id,
    )
    return last_id, last_sampled_at


def _infer_ts_for_bucket(bkey: str, unit: Unit) -> datetime:
    """Best-effort timestamp inside the bucket, for score computation."""
    if unit == "hour":
        return datetime.strptime(bkey, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
    if unit == "day":
        return datetime.strptime(bkey, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if unit == "week":
        # "YYYY-Www"
        year_s, week_s = bkey.split("-W")
        return datetime.fromisocalendar(int(year_s), int(week_s), 1).replace(
            tzinfo=timezone.utc
        )
    if unit == "month":
        return datetime.strptime(bkey, "%Y-%m").replace(tzinfo=timezone.utc)
    raise ValueError(f"Unknown unit: {unit}")
