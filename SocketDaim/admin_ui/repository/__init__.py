"""asyncpg-based repositories for the Admin UI (gw_admin role)."""

from __future__ import annotations

from .pool import create_pool
from .request_repo import StationRequestAdminRepository
from .station_repo import StationAdminRepository
from .video_repo import VideoAdminRepository
from .waypoint_repo import WaypointLabelAdminRepository

__all__ = [
    "create_pool",
    "StationAdminRepository",
    "StationRequestAdminRepository",
    "VideoAdminRepository",
    "WaypointLabelAdminRepository",
]
