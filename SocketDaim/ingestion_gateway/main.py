"""Ingestion Gateway entry point.

Two operating modes selected by ``IGW_PROTOCOL``:

* ``loas`` (default) — LOAS Tfoi v4a vendor protocol.  Opens two listeners
  (DUST = 9100, CCTV = 13320), runs a background Correlator that pairs
  cctv_frame rows to dust_inspection rows by time window.  Single-AMR
  mode; multi-AMR work is intentionally deferred.

* ``standard`` — legacy gw_proto protocol on a single TCP port.  Uses
  station-name based routing and the JSON/JSON-binary message types.
  Used by the MockSensor + Dumopro analysis flow.

There is **no HTTP server** here — Station CRUD belongs to a separate
admin tool, and Consumers read directly from the shared database with a
read-only role.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

import structlog
from gw_proto import (
    Message,
    MessageType,
    SessionContext,
    TcpServer,
    get_codec,
)
from gw_proto.transport import LoasCctvTcpServer, LoasDustTcpServer

from .config import IngestionSettings
from .correlator import FrameCorrelator
from .handler import (
    ControlHandler,
    LoasCctvHandler,
    LoasDustHandler,
    SensorHandler,
    VideoHandler,
)
from .logging_config import configure_logging
from .repository import (
    CctvFrameRepository,
    DustInspectionRepository,
    IngestionLogRepository,
    SensorRepository,
    StationRepository,
    StationRequestRepository,
    VideoRepository,
    create_pool,
)
from .session import SessionRegistry


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

async def run() -> None:
    settings = IngestionSettings()
    configure_logging(settings.log_level, settings.log_format)
    logger = structlog.get_logger(__name__)
    logger.info(
        "starting_ingestion_gateway",
        protocol=settings.protocol,
        storage_root=settings.storage_root,
    )

    pool = await create_pool(settings)
    logger.info("db_pool_ready", db_host=settings.db_host, db_name=settings.db_name)

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event, logger)

    try:
        if settings.protocol == "standard":
            await _run_standard(settings, pool, stop_event, logger)
        elif settings.protocol == "loas":
            await _run_loas(settings, pool, stop_event, logger)
        else:
            raise ValueError(
                f"Unknown IGW_PROTOCOL: {settings.protocol!r} "
                "(expected 'standard' or 'loas')"
            )
    finally:
        await pool.close()
        logger.info("ingestion_gateway_stopped")


def _install_signal_handlers(stop_event: asyncio.Event, logger) -> None:
    """SIGTERM/SIGINT → set stop_event (POSIX only; Windows uses CTRL+C)."""
    if sys.platform == "win32":
        return
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(
                sig,
                lambda: (logger.info("shutdown_requested"), stop_event.set()),
            )
        except NotImplementedError:
            pass


# ---------------------------------------------------------------------------
# protocol = "standard"
# ---------------------------------------------------------------------------

async def _run_standard(
    settings: IngestionSettings,
    pool,
    stop_event: asyncio.Event,
    logger,
) -> None:
    logger.info(
        "standard_mode_starting",
        tcp_host=settings.tcp_host,
        tcp_port=settings.tcp_port,
    )

    # Repositories
    station_repo = StationRepository(pool)
    video_repo = VideoRepository(pool)
    sensor_repo = SensorRepository(pool)
    log_repo = IngestionLogRepository(pool)
    request_repo = StationRequestRepository(pool)

    # Handlers
    control_handler = ControlHandler(log_repo)
    sensor_handler = SensorHandler(sensor_repo, station_repo, log_repo, request_repo)
    video_handler = VideoHandler(
        video_repo, station_repo, log_repo, request_repo, settings.storage_root
    )

    registry = SessionRegistry()
    codec = get_codec(settings.protocol)

    async def dispatch(message: Message, ctx: SessionContext) -> Message | None:
        session = registry.get_or_create(ctx.session_id, ctx.peer)

        match message.msg_type:
            case MessageType.VIDEO_CHUNK:
                return await video_handler.handle_chunk(message, session)
            case MessageType.VIDEO_COMPLETE:
                return await video_handler.handle_complete(message, session)
            case MessageType.SENSOR_SAMPLE:
                return await sensor_handler.handle(message, session)
            case MessageType.HEARTBEAT:
                return await control_handler.handle_heartbeat(message, session)
            case MessageType.ERROR:
                await control_handler.handle_error(message, session)
                return None
            case MessageType.ACK:
                return None
            case _:
                logger.warning(
                    "unhandled_message_type",
                    msg_type=int(message.msg_type),
                    session_id=session.session_id[:8],
                )
                return Message.error(f"Unhandled type: 0x{int(message.msg_type):04X}")

    server = TcpServer(
        host=settings.tcp_host,
        port=settings.tcp_port,
        codec=codec,
        handler=dispatch,
    )

    server_task = asyncio.create_task(server.start())
    await stop_event.wait()

    await server.stop()
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# protocol = "loas"
# ---------------------------------------------------------------------------

async def _run_loas(
    settings: IngestionSettings,
    pool,
    stop_event: asyncio.Event,
    logger,
) -> None:
    logger.info(
        "loas_mode_starting",
        dust_port=settings.loas_dust_port,
        cctv_port=settings.loas_cctv_port,
        amr_id=settings.loas_amr_id,
        window_before_sec=settings.loas_window_before_sec,
        window_after_sec=settings.loas_window_after_sec,
        correlator_interval_sec=settings.loas_correlator_interval_sec,
    )

    dust_repo = DustInspectionRepository(pool)
    cctv_repo = CctvFrameRepository(pool)

    dump_dir = (
        Path(settings.storage_root) / settings.loas_dust_dump_subdir
        if settings.loas_dust_dump_enabled
        else None
    )
    if dump_dir is not None:
        logger.info(
            "loas_dust_dump_enabled",
            dir=str(dump_dir),
            interval_sec=settings.loas_dust_dump_interval_sec,
        )
    dust_handler = LoasDustHandler(
        dust_repo,
        dump_dir=dump_dir,
        dump_interval_sec=settings.loas_dust_dump_interval_sec,
    )
    cctv_handler = LoasCctvHandler(
        cctv_repo,
        Path(settings.storage_root),
        settings.loas_amr_id,
        subdir=settings.loas_cctv_subdir,
    )

    dust_server = LoasDustTcpServer(
        settings.tcp_host, settings.loas_dust_port, dust_handler.on_frame
    )
    cctv_server = LoasCctvTcpServer(
        settings.tcp_host, settings.loas_cctv_port, cctv_handler.on_frame
    )
    correlator = FrameCorrelator(
        pool,
        interval_sec=settings.loas_correlator_interval_sec,
        before_sec=settings.loas_window_before_sec,
        after_sec=settings.loas_window_after_sec,
        lookback_sec=settings.loas_lookback_sec,
    )

    dust_task = asyncio.create_task(dust_server.start(), name="loas_dust_server")
    cctv_task = asyncio.create_task(cctv_server.start(), name="loas_cctv_server")
    corr_task = asyncio.create_task(correlator.run(), name="loas_correlator")
    stop_task = asyncio.create_task(stop_event.wait(), name="stop_event_wait")

    # Wait for SIGTERM OR for any listener / correlator task to die.
    # Earlier revisions only awaited stop_event, so if cctv_server.start()
    # raised (e.g. port already bound, start_server OSError) the exception
    # was buried in the orphan task and the process stayed up serving only
    # DUST — silent partial failure.  Now any task dying wakes the main
    # coroutine, the error is logged, and the process exits so docker
    # restart_policy can recover.
    done, _pending = await asyncio.wait(
        {stop_task, dust_task, cctv_task, corr_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    listener_died_exc: BaseException | None = None
    for task in done:
        if task is stop_task:
            continue
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            logger.error(
                "loas_listener_task_died",
                task_name=task.get_name(),
                err=repr(exc),
            )
            listener_died_exc = exc

    if not stop_task.done():
        stop_task.cancel()

    # Stop in reverse boot order.  Correlator first so its in-flight UPDATE
    # finishes before we tear the pool down later.
    correlator.stop()
    try:
        await asyncio.wait_for(corr_task, timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        corr_task.cancel()
    except Exception:
        pass

    try:
        await dust_server.stop()
    except Exception:
        logger.exception("dust_server_stop_failed")
    try:
        await cctv_server.stop()
    except Exception:
        logger.exception("cctv_server_stop_failed")

    for task in (dust_task, cctv_task):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # If a listener died on its own, re-raise so the process exits non-zero
    # and docker's restart_policy=unless-stopped boots a fresh container.
    if listener_died_exc is not None:
        raise listener_died_exc


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
