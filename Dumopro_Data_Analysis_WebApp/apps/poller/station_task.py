from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import asyncpg
import numpy as np

from dumopro_core.buckets import UNIT_LIST, bucket_key, bucket_score
from dumopro_core.candles import compute_box_stats
from dumopro_core.config import Settings
from dumopro_core.db import fetch_samples_since
from dumopro_core.keys import Unit
from dumopro_core.models import SampleRow, StationInfo
from dumopro_core.redis_client import RedisClient
from dumopro_core.serialize import candle_to_json

from .backfill import cold_start
from .freezer import GraceFreezer

log = logging.getLogger(__name__)


class StationTask:
    """Per-station polling loop. Cold/warm start branch, grace-period freezing,
    live stats recomputation + pub/sub publish.
    """

    def __init__(
        self,
        station: StationInfo,
        pool: asyncpg.Pool,
        redis: RedisClient,
        settings: Settings,
        cold_start_signal: asyncio.Event | None = None,
    ) -> None:
        self.station = station
        self.pool = pool
        self.redis = redis
        self.settings = settings
        self._stop = asyncio.Event()
        self._consecutive_failures = 0
        self._freezer = GraceFreezer(grace_seconds=settings.grace_period_sec)
        self._last_bucket_keys: dict[Unit, str] = {}
        self._dirty: set[tuple[Unit, str]] = set()
        self._cold_done = cold_start_signal

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info(
            "station_task.start station=%s id=%s",
            self.station.station_name,
            self.station.station_id,
        )
        last_id, cursor_data = await self.redis.get_cursor(self.station.station_name)
        cold = last_id == 0 and "last_sampled_at" not in cursor_data

        if cold:
            try:
                last_id, last_sampled_at = await cold_start(
                    self.station, self.pool, self.redis, self.settings.measurement_type,
                    sample_source=self.settings.sample_source,
                )
                if last_sampled_at is not None:
                    self._seed_last_buckets(last_sampled_at)
            except Exception:
                log.exception(
                    "station_task.cold_start_failed station=%s",
                    self.station.station_name,
                )
                raise
        else:
            log.info(
                "station_task.warm_start station=%s last_id=%d",
                self.station.station_name,
                last_id,
            )
            last_sampled_at_str = cursor_data.get("last_sampled_at")
            if last_sampled_at_str:
                try:
                    self._seed_last_buckets(datetime.fromisoformat(last_sampled_at_str))
                except ValueError:
                    pass

        if self._cold_done is not None:
            self._cold_done.set()

        while not self._stop.is_set():
            try:
                last_id = await self._tick(last_id)
                self._consecutive_failures = 0
            except Exception:
                self._consecutive_failures += 1
                log.exception(
                    "station_task.error station=%s failures=%d",
                    self.station.station_name,
                    self._consecutive_failures,
                )
                if self._consecutive_failures >= self.settings.consecutive_failure_cap:
                    log.error(
                        "station_task.abort station=%s after %d consecutive failures",
                        self.station.station_name,
                        self._consecutive_failures,
                    )
                    await self._publish_stalled("consecutive_failure_cap_reached")
                    return
                await asyncio.sleep(self.settings.restart_wait_sec)
                continue

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.poll_interval_sec)
            except asyncio.TimeoutError:
                pass

    def _seed_last_buckets(self, ts: datetime) -> None:
        for unit in UNIT_LIST:
            self._last_bucket_keys[unit] = bucket_key(ts, unit)

    async def _tick(self, last_id: int) -> int:
        samples = await fetch_samples_since(
            self.pool,
            self.station.station_id,
            last_id,
            self.settings.measurement_type,
            self.settings.poll_batch_limit,
            source=self.settings.sample_source,
        )
        if samples:
            for s in samples:
                for unit in UNIT_LIST:
                    bkey = bucket_key(s.sampled_at, unit)
                    prev = self._last_bucket_keys.get(unit)
                    if prev is not None and prev != bkey:
                        # Boundary crossed: schedule previous bucket for freeze
                        self._freezer.schedule(unit, prev)
                    self._last_bucket_keys[unit] = bkey
                    await self.redis.add_live_raw(
                        self.station.station_name, unit, bkey, s.id, s.value
                    )
                    self._dirty.add((unit, bkey))

            last_id = samples[-1].id
            await self.redis.set_cursor(
                self.station.station_name,
                last_id=last_id,
                last_sampled_at=samples[-1].sampled_at,
            )
            await self._recompute_dirty(samples[-1].sampled_at)

            log.info(
                "station_task.batch station=%s count=%d last_id=%d",
                self.station.station_name,
                len(samples),
                last_id,
            )

        await self._process_freezes()
        return last_id

    async def _recompute_dirty(self, updated_at: datetime) -> None:
        while self._dirty:
            unit, bkey = self._dirty.pop()
            values = await self.redis.get_live_raw_values(
                self.station.station_name, unit, bkey
            )
            if not values:
                continue
            stats = compute_box_stats(np.array(values, dtype=np.float64))
            payload_json = candle_to_json(stats, updated_at=updated_at)
            await self.redis.set_live_stats(
                self.station.station_name, unit, bkey, payload_json
            )
            await self.redis.publish_candle_event(
                self.station.station_name,
                {
                    "type": "candle_update",
                    "station": self.station.station_name,
                    "unit": unit,
                    "bucket_key": bkey,
                    "stats": _parse(payload_json),
                    "updated_at": updated_at.isoformat(),
                },
            )

    async def _process_freezes(self) -> None:
        for unit, bkey in self._freezer.due():
            values = await self.redis.get_live_raw_values(
                self.station.station_name, unit, bkey
            )
            if not values:
                self._freezer.drop(unit, bkey)
                continue
            stats = compute_box_stats(np.array(values, dtype=np.float64))
            payload_json = candle_to_json(stats)
            # Score: use bucket-start timestamp derived from bucket_key
            from .backfill import _infer_ts_for_bucket  # local import to avoid cycle risk

            score = bucket_score(_infer_ts_for_bucket(bkey, unit), unit)
            await self.redis.freeze_bucket(
                self.station.station_name, unit, bkey, payload_json, score
            )
            self._freezer.drop(unit, bkey)
            await self.redis.publish_candle_event(
                self.station.station_name,
                {
                    "type": "candle_frozen",
                    "station": self.station.station_name,
                    "unit": unit,
                    "bucket_key": bkey,
                    "stats": _parse(payload_json),
                },
            )
            log.info(
                "station_task.frozen station=%s unit=%s bucket=%s",
                self.station.station_name,
                unit,
                bkey,
            )

    async def _publish_stalled(self, reason: str) -> None:
        try:
            await self.redis.publish_candle_event(
                self.station.station_name,
                {
                    "type": "station_stalled",
                    "station": self.station.station_name,
                    "reason": reason,
                },
            )
        except Exception:
            log.exception("station_task.stalled_publish_failed station=%s", self.station.station_name)


def _parse(raw: str) -> dict:
    import json

    return json.loads(raw)
