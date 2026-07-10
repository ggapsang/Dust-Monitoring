"""Sensor sample repository – INSERT only."""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg


class SensorRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(
        self,
        *,
        station_id: str | uuid.UUID,
        measurement_type: str,
        value: float,
        unit: str,
        sampled_at: datetime,
    ) -> None:
        sid = station_id if isinstance(station_id, uuid.UUID) else uuid.UUID(str(station_id))
        await self._pool.execute(
            """
            INSERT INTO sensor_sample
                (station_id, measurement_type, value, unit, sampled_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            sid, measurement_type, value, unit, sampled_at,
        )
