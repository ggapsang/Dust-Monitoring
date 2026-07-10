"""Video repository – INSERT only."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg


class VideoRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(
        self,
        *,
        video_id: str | uuid.UUID,
        station_id: str | uuid.UUID,
        file_path: str,
        amr_id: str | None = None,
        captured_at: datetime | None = None,
        duration_sec: float | None = None,
        resolution: str | None = None,
        source_format: str | None = None,
        amr_position: dict[str, Any] | None = None,
        quality_check_result: dict[str, Any] | None = None,
        is_valid: bool = True,
        is_excluded: bool = False,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO video (
                video_id, station_id, amr_id, captured_at, file_path,
                duration_sec, resolution, source_format,
                amr_position, quality_check_result, is_valid, is_excluded
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11, $12)
            """,
            _uuid(video_id),
            _uuid(station_id),
            amr_id,
            captured_at,
            file_path,
            duration_sec,
            resolution,
            source_format,
            json.dumps(amr_position) if amr_position is not None else None,
            json.dumps(quality_check_result) if quality_check_result is not None else None,
            is_valid,
            is_excluded,
        )


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
