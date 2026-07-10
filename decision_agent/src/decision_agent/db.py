"""asyncpg pool factory."""

from __future__ import annotations

import asyncpg

from .config import DASettings


async def create_pool(settings: DASettings) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    assert pool is not None
    return pool


async def create_gateway_pool(settings: DASettings) -> asyncpg.Pool | None:
    """Read-only pool to SocketDaim's gateway_db for station-label lookups.

    Best-effort: returns ``None`` (instead of raising) if the connection can't
    be established, so the agent still boots and the admin UI simply falls back
    to 'TGT-?' for the station column.
    """
    try:
        return await asyncpg.create_pool(
            dsn=settings.gateway_dsn,
            min_size=1,
            max_size=max(2, settings.db_pool_max // 2),
        )
    except Exception:  # noqa: BLE001 — degrade gracefully, caller logs
        return None
