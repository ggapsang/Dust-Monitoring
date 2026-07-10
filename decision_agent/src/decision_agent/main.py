"""Decision Agent entry point."""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

from .admin import build_app, run_admin_server
from .config import DASettings
from .db import create_gateway_pool, create_pool
from .judge import Judge
from .logging_config import configure_logging
from .poller import Poller
from .repository import DecisionRepository, GatewayRepository
from .role_resolver import RoleResolver


async def run() -> None:
    settings = DASettings()
    configure_logging(settings.log_level, settings.log_format)
    logger = structlog.get_logger(__name__)
    logger.info(
        "starting_decision_agent",
        db_host=settings.db_host,
        db_name=settings.db_name,
        poll_interval_sec=settings.poll_interval_sec,
        admin_port=settings.admin_port,
    )

    pool = await create_pool(settings)
    logger.info("db_pool_ready", db_host=settings.db_host, db_name=settings.db_name)

    # Read-only gateway_db pool for station-label lookups (admin UI only).
    # Best-effort: None if unreachable → station column falls back to 'TGT-?'.
    gateway_pool = await create_gateway_pool(settings)
    gateway_repo = GatewayRepository(gateway_pool) if gateway_pool is not None else None
    if gateway_pool is not None:
        logger.info(
            "gateway_db_pool_ready",
            db_host=settings.gateway_db_host,
            db_name=settings.gateway_db_name,
        )
    else:
        logger.warning(
            "gateway_db_unavailable_station_labels_disabled",
            db_host=settings.gateway_db_host,
        )

    repo = DecisionRepository(pool)

    judge = Judge(pool)
    await judge.load()

    role_resolver = RoleResolver(pool, refresh_sec=settings.role_refresh_sec)
    await role_resolver.start()

    poller = Poller(
        repo,
        judge,
        role_resolver,
        interval_sec=settings.poll_interval_sec,
        batch_size=settings.batch_size,
    )

    admin_app = build_app(settings, pool, repo, judge, role_resolver, gateway_repo)

    stop_event = asyncio.Event()

    def _request_stop() -> None:
        logger.info("shutdown_requested")
        stop_event.set()

    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                pass

    try:
        await asyncio.gather(
            poller.run(stop_event),
            run_admin_server(
                admin_app,
                host=settings.admin_host,
                port=settings.admin_port,
                stop_event=stop_event,
            ),
        )
    finally:
        await role_resolver.stop()
        await pool.close()
        if gateway_pool is not None:
            await gateway_pool.close()
        logger.info("decision_agent_stopped")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
