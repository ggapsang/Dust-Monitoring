"""Health + monitoring HTTP server (FastAPI).

Exposes queue depth, processing counters, and DB connectivity so the queue
backlog / attempts distribution can be observed (PoolerTran_설계.md §13).
Runs alongside the poller via asyncio.gather.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .poller import Poller
from .repository import QueueRepository

logger = structlog.get_logger(__name__)


def build_app(
    queue: QueueRepository,
    poller: Poller,
    gw_pool: asyncpg.Pool,
    write_pool: asyncpg.Pool | None,
    write_db_name: str = "decision_db",
) -> FastAPI:
    """write_pool = 결과 기록 대상 풀(decision_db).  write_db_name 으로 health 키
    라벨을 맞춘다.  None 이면 해당 점검 생략."""
    app = FastAPI(title="PoolerTran Health", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        gw_ok = await _ping(gw_pool)
        write_ok = await _ping(write_pool) if write_pool is not None else True
        depth: int | None = None
        if gw_ok:
            try:
                depth = await queue.depth()
            except Exception:  # noqa: BLE001
                gw_ok = False
        ok = gw_ok and write_ok
        body: dict[str, Any] = {
            "status": "ok" if ok else "degraded",
            "gateway_db": gw_ok,
            write_db_name: write_ok,
            "queue_depth": depth,
            "stats": poller.stats,
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    return app


async def _ping(pool: asyncpg.Pool) -> bool:
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


async def run_health_server(
    app: FastAPI, host: str, port: int, stop_event: asyncio.Event
) -> None:
    import uvicorn

    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", access_log=False, lifespan="off"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    try:
        await stop_event.wait()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except asyncio.TimeoutError:
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
