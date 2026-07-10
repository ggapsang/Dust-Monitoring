"""Station repository – SELECT only.

The Gateway does NOT own station lifecycle.  Wire messages identify a
station by its ``station_name`` (a stable, human-meaningful key shared
between sender and receiver).  This repository looks the name up to the
``station_id`` UUID used as the FK in ``video`` / ``sensor_sample``.

INSERT / UPDATE / DELETE on station is the responsibility of a separate
admin tool and is enforced at the PostgreSQL role level (gw_writer has
SELECT only).
"""

from __future__ import annotations

import uuid

import asyncpg


class StationRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def lookup_by_name(self, station_name: str) -> uuid.UUID | None:
        """Return the station_id (UUID) for an active station_name, or
        None if the name is unknown or the station is inactive."""
        row = await self._pool.fetchrow(
            """
            SELECT station_id FROM station
             WHERE station_name = $1 AND status <> 'inactive'
            """,
            station_name,
        )
        return row["station_id"] if row else None
