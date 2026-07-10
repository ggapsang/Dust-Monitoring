"""Ingestion log repository – INSERT only."""

from __future__ import annotations

import uuid

import asyncpg


class IngestionLogRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(
        self,
        *,
        station_id: str | uuid.UUID | None,
        message_type: str,
        status: str,                    # 'success' | 'error'
        error_message: str | None = None,
    ) -> None:
        sid: uuid.UUID | None
        if station_id is None:
            sid = None
        elif isinstance(station_id, uuid.UUID):
            sid = station_id
        else:
            try:
                sid = uuid.UUID(str(station_id))
            except (ValueError, AttributeError, TypeError):
                sid = None

        await self._pool.execute(
            """
            INSERT INTO ingestion_log
                (station_id, message_type, status, error_message)
            VALUES ($1, $2, $3, $4)
            """,
            sid, message_type, status, error_message,
        )
