from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dumopro_core.config import get_settings
from dumopro_core.db import init_pool
from dumopro_core.redis_client import RedisClient

from .routes import chart, health, raw, regression, settings as settings_route, stations, stream
from .services.sse_broadcaster import Broadcaster

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("api.main")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("api.boot redis=%s", settings.redis_url)
    redis = RedisClient(settings.redis_url)
    await redis.ping()
    broadcaster = Broadcaster(redis)
    pool = await init_pool(settings.pg_dsn, min_size=1, max_size=3)
    app.state.redis = redis
    app.state.broadcaster = broadcaster
    app.state.pool = pool
    app.state.settings = settings
    try:
        yield
    finally:
        await broadcaster.close()
        await redis.close()
        await pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Dumopro Data Analysis", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(stations.router)
    app.include_router(chart.router)
    app.include_router(stream.router)
    app.include_router(regression.router)
    app.include_router(settings_route.router)
    app.include_router(raw.router)

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    else:
        log.warning("frontend dir not found: %s", FRONTEND_DIR)

    return app


app = create_app()
