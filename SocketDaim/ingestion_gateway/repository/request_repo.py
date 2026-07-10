"""station_request repository (gw_writer side).

Called by handlers when StationRepository.lookup_by_name() returns None.
The gateway only INSERTs new rows or bumps attempts/last_seen via
ON CONFLICT.  status transitions (pending → approved/rejected) belong
to the admin tool, never to this code path.

Wire-level identity is ``station_name``; the table PK matches.
"""

from __future__ import annotations

import asyncpg


class StationRequestRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert(self, station_name: str) -> None:
        if not station_name:
            return
        await self._pool.execute(
            """
            INSERT INTO station_request (station_name) VALUES ($1)
            ON CONFLICT (station_name) DO UPDATE
                SET last_seen = NOW(),
                    attempts  = station_request.attempts + 1
            """,
            station_name,
        )
