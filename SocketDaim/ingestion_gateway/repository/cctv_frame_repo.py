"""cctv_frame repository.

INSERT-only from the ingestion side.  The pairing UPDATE (setting
``dust_inspection_id`` and ``paired_at``) is owned by the Correlator
module, which is implemented in a separate PR but already has its column
grants in the migration.
"""

from __future__ import annotations

from datetime import datetime

import asyncpg

_INSERT_SQL = """
    INSERT INTO cctv_frame (
        received_at, amr_id, source_ip, resolution, file_path, byte_size
    ) VALUES (
        COALESCE($1, clock_timestamp()),
        $2, $3, $4, $5, $6
    )
    RETURNING id
"""


class CctvFrameRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(
        self,
        *,
        amr_id: str,
        source_ip: str | None,
        resolution: str,
        file_path: str,
        byte_size: int,
        received_at: datetime | None = None,
    ) -> int:
        """Insert one cctv_frame row, return its ``id``.

        ``received_at`` is the canonical pairing key — passing an explicit
        value lets the handler keep file-name and row timestamps in lock
        step.  ``None`` defers to ``clock_timestamp()`` at the DB.
        """
        return await self._pool.fetchval(
            _INSERT_SQL,
            received_at,
            amr_id,
            source_ip,
            resolution,
            file_path,
            byte_size,
        )
