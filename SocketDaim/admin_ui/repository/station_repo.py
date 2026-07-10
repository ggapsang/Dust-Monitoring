"""Station CRUD repository (gw_admin role)."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from ..seed_data import SAMPLE_STATIONS

_VALID_STATUS = ("collecting", "waiting", "training", "inferring", "inactive")


class StationAdminRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT station_id, station_name, location_info, amr_id,
                   capture_cycle, description, status, created_at, updated_at
              FROM station
          ORDER BY created_at DESC
            """
        )
        return [dict(r) for r in rows]

    async def get(self, station_id: uuid.UUID) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """
            SELECT station_id, station_name, location_info, amr_id,
                   capture_cycle, description, status, created_at, updated_at
              FROM station
             WHERE station_id = $1
            """,
            station_id,
        )
        return dict(row) if row else None

    async def create(
        self,
        *,
        station_name: str,
        location_info: str | None,
        amr_id: str | None,
        capture_cycle: int | None,
        description: str | None,
        status: str,
        station_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Create a new station.  station_id optional — when provided
        (e.g. from approve flow), the row reuses the UUID the device has
        been sending."""
        if status not in _VALID_STATUS:
            raise ValueError(f"invalid status: {status}")

        if station_id is None:
            row = await self._pool.fetchrow(
                """
                INSERT INTO station
                    (station_name, location_info, amr_id, capture_cycle,
                     description, status)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING station_id, station_name, location_info, amr_id,
                          capture_cycle, description, status,
                          created_at, updated_at
                """,
                station_name, location_info, amr_id, capture_cycle,
                description, status,
            )
        else:
            row = await self._pool.fetchrow(
                """
                INSERT INTO station
                    (station_id, station_name, location_info, amr_id,
                     capture_cycle, description, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING station_id, station_name, location_info, amr_id,
                          capture_cycle, description, status,
                          created_at, updated_at
                """,
                station_id, station_name, location_info, amr_id,
                capture_cycle, description, status,
            )
        assert row is not None
        return dict(row)

    async def update(
        self,
        station_id: uuid.UUID,
        *,
        station_name: str | None = None,
        location_info: str | None = None,
        amr_id: str | None = None,
        capture_cycle: int | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch the row.  None = leave unchanged."""
        if status is not None and status not in _VALID_STATUS:
            raise ValueError(f"invalid status: {status}")

        # COALESCE pattern keeps unchanged columns intact.
        row = await self._pool.fetchrow(
            """
            UPDATE station
               SET station_name  = COALESCE($2, station_name),
                   location_info = COALESCE($3, location_info),
                   amr_id        = COALESCE($4, amr_id),
                   capture_cycle = COALESCE($5, capture_cycle),
                   description   = COALESCE($6, description),
                   status        = COALESCE($7, status),
                   updated_at    = NOW()
             WHERE station_id = $1
         RETURNING station_id, station_name, location_info, amr_id,
                   capture_cycle, description, status,
                   created_at, updated_at
            """,
            station_id, station_name, location_info, amr_id,
            capture_cycle, description, status,
        )
        return dict(row) if row else None

    async def delete(self, station_id: uuid.UUID) -> bool:
        """Hard-delete.  Raises asyncpg.ForeignKeyViolationError if
        video/sensor_sample rows reference this station — callers should
        translate to HTTP 409."""
        result = await self._pool.execute(
            "DELETE FROM station WHERE station_id = $1",
            station_id,
        )
        # asyncpg returns "DELETE N"
        return result.endswith(" 1")

    async def seed_samples(self) -> dict[str, int]:
        """Idempotent INSERT of the 4 sample stations.  Returns counts."""
        inserted = 0
        skipped = 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for s in SAMPLE_STATIONS:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO station
                            (station_name, location_info, capture_cycle, status)
                        SELECT $1, $2, $3, $4
                         WHERE NOT EXISTS (
                             SELECT 1 FROM station WHERE station_name = $1
                         )
                     RETURNING station_id
                        """,
                        s.station_name, s.location_info, s.capture_cycle, s.status,
                    )
                    if row is None:
                        skipped += 1
                    else:
                        inserted += 1
        return {"inserted": inserted, "skipped": skipped}

    async def status_counts(self) -> dict[str, int]:
        row = await self._pool.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status <> 'inactive') AS active,
                COUNT(*) FILTER (WHERE status =  'inactive') AS inactive,
                COUNT(*)                                     AS total
              FROM station
            """
        )
        return {k: int(v) for k, v in dict(row).items()}

    async def recent_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            """
            SELECT created_at, station_id, message_type, error_message
              FROM ingestion_log
             WHERE status = 'error'
          ORDER BY created_at DESC
             LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]
