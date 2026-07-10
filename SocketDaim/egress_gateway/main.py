"""Egress Gateway entry point.

Single-purpose: pull decisions from the decision DB and upsert them into the
LOAS-side MariaDB.  No HTTP server.
"""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

from .config import EgressSettings
from .logging_config import configure_logging
from .outbox import Outbox
from .poller import Poller
from .repository import (
    DecisionRepository,
    GatewayRepository,
    create_gateway_pool,
    create_pool,
)
from .sender import Sender
from .sink import MariaDbSink


async def run() -> None:
    settings = EgressSettings()
    configure_logging(settings.log_level, settings.log_format)
    logger = structlog.get_logger(__name__)
    logger.info(
        "starting_egress_gateway",
        target_db_host=settings.target_db_host,
        target_db_name=settings.target_db_name,
        db_host=settings.db_host,
    )

    # 판정 소스 pool (egress_role) — decision_db
    pool = await create_pool(settings)
    # dust_inspection 24컬럼 읽기 pool (gw_reader) — gateway_db (cross-DB)
    gw_pool = await create_gateway_pool(settings)
    logger.info(
        "db_pools_ready",
        decision_db=f"{settings.db_host}/{settings.db_name}",
        gateway_db=f"{settings.gw_db_host}/{settings.gw_db_name}",
    )

    repo = DecisionRepository(pool)
    gateway_repo = GatewayRepository(gw_pool)

    # Outbox (SQLite)
    outbox = Outbox(settings.outbox_path)
    await outbox.open()
    logger.info("outbox_ready", path=settings.outbox_path, count=await outbox.count())

    # 타깃: LOAS 측 MariaDB t_inspection (INSERT)
    sink = MariaDbSink(
        host=settings.target_db_host,
        port=settings.target_db_port,
        db=settings.target_db_name,
        table=settings.target_db_table,
        user=settings.target_db_user,
        password=settings.target_db_password,
        pool_min=settings.target_db_pool_min,
        pool_max=settings.target_db_pool_max,
        sql_log=settings.sql_log_enable,
    )
    await sink.open()
    if settings.sql_log_enable:
        logger.info("egress_sql_logging_enabled")

    sender = Sender(
        sink, outbox, repo, gateway_repo,
        allowed_event_ids=settings.allowed_event_ids,
    )
    logger.info("egress_event_id_filter", allowed=sorted(settings.allowed_event_ids))

    # Replay any outbox rows left over from a previous run
    await _drain_outbox(outbox, sender, logger)

    poller = Poller(
        repo, sender,
        interval_sec=settings.poll_interval_sec,
        batch_size=settings.batch_size,
    )

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
        await poller.run(stop_event)
    finally:
        await sink.close()
        await outbox.close()
        await pool.close()
        await gw_pool.close()
        logger.info("egress_gateway_stopped")


async def _drain_outbox(outbox: Outbox, sender: Sender, logger) -> None:
    """Replay rows left in outbox from a prior run."""
    rows = []
    async for row in outbox.iter_pending():
        rows.append(row)
    if not rows:
        return
    logger.info("outbox_drain_start", pending=len(rows))
    drained = 0
    for row in rows:
        ok = await sender.replay(row.decision_id, row.msg_type, row.payload)
        if ok:
            drained += 1
        else:
            break  # stop on first failure; will retry on next tick / restart
    logger.info("outbox_drain_done", drained=drained, remaining=len(rows) - drained)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
