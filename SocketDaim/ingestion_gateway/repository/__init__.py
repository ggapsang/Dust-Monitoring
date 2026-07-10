"""asyncpg-based repositories for the Ingestion Gateway."""

from __future__ import annotations

import asyncpg

from ..config import IngestionSettings


async def create_pool(settings: IngestionSettings) -> asyncpg.Pool:
    """Create an asyncpg connection pool using the gw_writer role."""
    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    assert pool is not None
    return pool


from .cctv_frame_repo import CctvFrameRepository  # noqa: E402
from .dust_inspection_repo import DustInspectionRepository  # noqa: E402
from .log_repo import IngestionLogRepository  # noqa: E402
from .request_repo import StationRequestRepository  # noqa: E402
from .sensor_repo import SensorRepository  # noqa: E402
from .station_repo import StationRepository  # noqa: E402
from .video_repo import VideoRepository  # noqa: E402

__all__ = [
    "create_pool",
    "CctvFrameRepository",
    "DustInspectionRepository",
    "IngestionLogRepository",
    "SensorRepository",
    "StationRepository",
    "StationRequestRepository",
    "VideoRepository",
]
