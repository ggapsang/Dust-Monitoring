"""Admin UI entry point — starts uvicorn serving the FastAPI app."""

from __future__ import annotations

import asyncio

import structlog
import uvicorn

from .app import build_app
from .config import AdminUISettings
from .logging_config import configure_logging
from .repository import (
    StationAdminRepository,
    StationRequestAdminRepository,
    VideoAdminRepository,
    WaypointLabelAdminRepository,
    create_pool,
)


async def run() -> None:
    settings = AdminUISettings()
    configure_logging(settings.log_level, settings.log_format)
    logger = structlog.get_logger(__name__)
    logger.info(
        "starting_admin_ui",
        http_host=settings.http_host, http_port=settings.http_port,
        db_host=settings.db_host, db_name=settings.db_name,
    )

    pool = await create_pool(settings)
    logger.info("db_pool_ready")

    station_repo = StationAdminRepository(pool)
    request_repo = StationRequestAdminRepository(pool)
    video_repo = VideoAdminRepository(pool)
    waypoint_repo = WaypointLabelAdminRepository(pool)
    app = build_app(
        settings, pool, station_repo, request_repo, video_repo, waypoint_repo
    )

    config = uvicorn.Config(
        app,
        host=settings.http_host,
        port=settings.http_port,
        log_level="warning",
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await pool.close()
        logger.info("admin_ui_stopped")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
