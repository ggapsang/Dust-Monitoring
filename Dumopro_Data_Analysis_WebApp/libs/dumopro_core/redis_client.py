from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

from . import keys as k
from .keys import Target, Unit


class RedisClient:
    def __init__(self, url: str) -> None:
        self._redis: Redis = Redis.from_url(url, decode_responses=True)

    @property
    def raw(self) -> Redis:
        return self._redis

    async def ping(self) -> bool:
        return await self._redis.ping()

    async def close(self) -> None:
        await self._redis.aclose()

    # --- cursor -----------------------------------------------------------

    async def get_cursor(self, station: str) -> tuple[int, dict[str, str]]:
        data = await self._redis.hgetall(k.cursor(station))
        last_id = int(data.get("last_id", 0))
        return last_id, data

    async def set_cursor(
        self,
        station: str,
        *,
        last_id: int,
        last_sampled_at: datetime | None = None,
        extra: dict[str, str] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"last_id": last_id}
        if last_sampled_at is not None:
            payload["last_sampled_at"] = last_sampled_at.isoformat()
        if extra:
            payload.update(extra)
        await self._redis.hset(k.cursor(station), mapping=payload)

    # --- live -------------------------------------------------------------

    async def add_live_raw(
        self,
        station: str,
        unit: Unit,
        bucket_key: str,
        sample_id: int,
        value: float,
    ) -> None:
        await self._redis.zadd(k.live_raw(station, unit, bucket_key), {str(sample_id): value})

    async def get_live_raw_values(
        self, station: str, unit: Unit, bucket_key: str
    ) -> list[float]:
        scores = await self._redis.zrange(
            k.live_raw(station, unit, bucket_key), 0, -1, withscores=True
        )
        return [float(score) for _, score in scores]

    async def set_live_stats(
        self, station: str, unit: Unit, bucket_key: str, stats_json: str
    ) -> None:
        await self._redis.set(k.live_stats(station, unit, bucket_key), stats_json)

    async def get_live_stats(
        self, station: str, unit: Unit, bucket_key: str
    ) -> dict | None:
        raw = await self._redis.get(k.live_stats(station, unit, bucket_key))
        return json.loads(raw) if raw else None

    # --- frozen -----------------------------------------------------------

    async def freeze_bucket(
        self,
        station: str,
        unit: Unit,
        bucket_key: str,
        stats_json: str,
        score: float,
    ) -> None:
        """Atomic: write frozen, add index, clear live artifacts."""
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(k.frozen(station, unit, bucket_key), stats_json)
        pipe.zadd(k.frozen_index(station, unit), {bucket_key: score})
        pipe.delete(k.live_raw(station, unit, bucket_key))
        pipe.delete(k.live_stats(station, unit, bucket_key))
        await pipe.execute()

    async def get_frozen_range(
        self, station: str, unit: Unit, min_score: float, max_score: float
    ) -> list[str]:
        return await self._redis.zrangebyscore(
            k.frozen_index(station, unit), min_score, max_score
        )

    async def get_frozen_stats(
        self, station: str, unit: Unit, bucket_key: str
    ) -> dict | None:
        raw = await self._redis.get(k.frozen(station, unit, bucket_key))
        return json.loads(raw) if raw else None

    # --- pub/sub ----------------------------------------------------------

    async def publish_candle_event(self, station: str, event: dict) -> None:
        await self._redis.publish(k.channel_candle(station), json.dumps(event))

    async def subscribe_candle(self, station: str):
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(k.channel_candle(station))
        return pubsub

    # --- residual ---------------------------------------------------------

    async def residual_push(
        self, station: str, unit: Unit, target: Target, residuals: list[float], cap: int = 10_000
    ) -> None:
        key = k.residual(station, unit, target)
        if not residuals:
            return
        pipe = self._redis.pipeline(transaction=True)
        pipe.rpush(key, *[str(v) for v in residuals])
        pipe.ltrim(key, -cap, -1)
        await pipe.execute()

    async def residual_all(
        self, station: str, unit: Unit, target: Target
    ) -> list[float]:
        raw = await self._redis.lrange(k.residual(station, unit, target), 0, -1)
        return [float(v) for v in raw]

    # --- config / stations list ------------------------------------------

    async def set_stations(self, stations: list[dict]) -> None:
        await self._redis.set(k.stations_list(), json.dumps(stations))

    async def get_stations(self) -> list[dict]:
        raw = await self._redis.get(k.stations_list())
        return json.loads(raw) if raw else []

    async def get_runtime_config(self) -> dict[str, str]:
        return await self._redis.hgetall(k.config_runtime())

    async def set_runtime_config(self, mapping: dict[str, str]) -> None:
        if mapping:
            await self._redis.hset(k.config_runtime(), mapping=mapping)

    # --- removed-station memory (for re-registration detection) ---------

    async def set_removed_station_id(self, station: str, station_id: str) -> None:
        await self._redis.hset(k.stations_removed(), station, station_id)

    async def get_removed_station_id(self, station: str) -> str | None:
        v = await self._redis.hget(k.stations_removed(), station)
        return v if v else None

    async def clear_removed_station_id(self, station: str) -> None:
        await self._redis.hdel(k.stations_removed(), station)

    # --- station re-registration conflicts -------------------------------

    async def set_pending_conflict(
        self, station: str, *, old_id: str, new_id: str
    ) -> None:
        """Mark a station_name as awaiting user decision because the
        underlying station_id changed.  Poller refuses to start a new
        StationTask for this name until the conflict is cleared."""
        await self._redis.hset(
            k.pending_conflict(station),
            mapping={
                "old_id": old_id,
                "new_id": new_id,
                "detected_at": datetime.now().isoformat(),
            },
        )

    async def get_pending_conflicts(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=k.pending_conflict_pattern(), count=200
            )
            for key in keys:
                data = await self._redis.hgetall(key)
                if not data:
                    continue
                # key format: "pending_conflict:{name}"
                name = key.split(":", 1)[1] if ":" in key else key
                out.append({
                    "station_name": name,
                    "old_id": data.get("old_id", ""),
                    "new_id": data.get("new_id", ""),
                    "detected_at": data.get("detected_at", ""),
                })
            if cursor == 0:
                break
        return out

    async def has_pending_conflict(self, station: str) -> bool:
        return bool(await self._redis.exists(k.pending_conflict(station)))

    async def clear_pending_conflict(self, station: str) -> bool:
        deleted = await self._redis.delete(k.pending_conflict(station))
        return bool(deleted)

    async def delete_station_data(self, station: str) -> int:
        """Delete every Redis key associated with a station_name.

        Used by 'start_fresh' resolution.  Removes cursor + live + frozen
        + residual keys so the next StationTask cold-starts cleanly.
        Pub/sub channels are not stored, so nothing to delete there.
        Also wipes the stations:removed marker so the same name won't
        immediately re-trigger as a conflict.
        """
        patterns = [
            k.cursor(station),                     # cursor:{name}
            f"live:raw:{station}:*",
            f"live:stats:{station}:*",
            f"frozen:{station}:*",                 # frozen:{name}:{unit}:{bucket}
            f"frozen:index:{station}:*",
            f"residual:{station}:*",
        ]
        total = 0
        for pattern in patterns:
            if "*" in pattern:
                cursor = 0
                while True:
                    cursor, found = await self._redis.scan(
                        cursor, match=pattern, count=200
                    )
                    if found:
                        total += await self._redis.delete(*found)
                    if cursor == 0:
                        break
            else:
                total += await self._redis.delete(pattern)
        await self.clear_removed_station_id(station)
        return total

    async def has_station_remnant_data(self, station: str) -> bool:
        """True if any historical data exists in Redis for this name —
        used to decide whether a freshly-appearing station_name is a
        re-registration (data exists) or a true greenfield station."""
        if await self._redis.exists(k.cursor(station)):
            return True
        # Cheap probe: any frozen index?
        for unit in ("hour", "day", "week", "month"):
            if await self._redis.exists(f"frozen:index:{station}:{unit}"):
                return True
        return False

    # --- pub/sub: station sync trigger ----------------------------------

    async def publish_sync_trigger(self) -> int:
        """API publishes here when the user clicks [Sync now].
        Returns the number of subscribers that received the message."""
        return await self._redis.publish(k.STATION_SYNC_CHANNEL, "1")

    async def subscribe_sync_trigger(self):
        """Poller subscribes to react immediately to [Sync now]."""
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(k.STATION_SYNC_CHANNEL)
        return pubsub
