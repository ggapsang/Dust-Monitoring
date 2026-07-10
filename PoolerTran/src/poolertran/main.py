"""PoolerTran entry point."""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

from .config import PTSettings
from .db import create_decision_pool, create_gateway_pool
from .health import build_app, run_health_server
from .logging_config import configure_logging
from .poller import Poller
from .repository import DecisionProducer, QueueRepository
from .rest_client import create_rest_client


async def run() -> None:
    settings = PTSettings()
    configure_logging(settings.log_level, settings.log_format)
    logger = structlog.get_logger(__name__)
    logger.info(
        "starting_poolertran",
        gw_db=f"{settings.gw_db_host}/{settings.gw_db_name}",
        decision_db=f"{settings.decision_db_host}/{settings.decision_db_name}",
        rest_url=settings.rest_url,
        poll_interval_sec=settings.poll_interval_sec,
        use_listen=settings.use_listen,
    )

    gw_pool = await create_gateway_pool(settings)

    # 결과는 decision_db 의 decision_record 로 적재(포이즌 메시지는 transfer_dlq).
    decision_pool = await create_decision_pool(settings)
    decision = DecisionProducer(decision_pool)
    logger.info(
        "decision_producer_ready",
        decision_db=f"{settings.decision_db_host}/{settings.decision_db_name}",
    )
    logger.info("db_pools_ready")

    queue = QueueRepository(gw_pool)
    rest = create_rest_client(settings)

    # 기동 시 큐 전체 삭제 — 재시작 시 이전 waypoint 작업은 버리고 현재/미래만 처리.
    # ⚠️ 단일 인스턴스 전제.  WARNING 으로 명시 로깅(파괴적 동작).
    if settings.clear_queue_on_start:
        deleted = await queue.clear()
        logger.warning("queue_cleared_on_start", deleted=deleted)

    poller = Poller(
        gw_pool,
        queue,
        rest,
        decision,
        gw_dsn=settings.gw_dsn,
        interval_sec=settings.poll_interval_sec,
        batch_size=settings.batch_size,
        max_attempts=settings.max_attempts,
        use_listen=settings.use_listen,
        init_waypoint_id=settings.init_waypoint_id,
        queue_max_age_sec=settings.queue_max_age_sec,
    )

    # health: gateway_db(큐/소스) + decision_db(결과) 연결 점검.
    health_app = build_app(queue, poller, gw_pool, decision_pool, "decision_db")

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
            run_health_server(
                health_app,
                host=settings.health_host,
                port=settings.health_port,
                stop_event=stop_event,
            ),
        )
    finally:
        await rest.close()
        await gw_pool.close()
        await decision_pool.close()
        logger.info("poolertran_stopped")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
