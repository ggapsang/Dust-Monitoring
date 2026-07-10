"""asyncpg pool helper for the Admin UI."""

from __future__ import annotations

import asyncpg

from ..config import AdminUISettings


async def create_pool(settings: AdminUISettings) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    assert pool is not None
    return pool
