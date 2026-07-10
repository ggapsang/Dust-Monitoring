from __future__ import annotations

import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class HealthState:
    def __init__(self) -> None:
        self.cold_start_done: bool = False
        self.tick_seen: bool = False
        self.station_count: int = 0

    def ready(self) -> bool:
        return self.cold_start_done and self.tick_seen


def build_app(state: HealthState) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> JSONResponse:
        body = {
            "status": "ready" if state.ready() else "starting",
            "cold_start_done": state.cold_start_done,
            "tick_seen": state.tick_seen,
            "station_count": state.station_count,
        }
        code = 200 if state.ready() else 503
        return JSONResponse(body, status_code=code)

    return app


async def serve(state: HealthState, port: int) -> None:
    config = uvicorn.Config(build_app(state), host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
