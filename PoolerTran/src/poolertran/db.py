"""asyncpg pool factories — gateway_db (read+queue) and decision_db (write)."""

from __future__ import annotations

import asyncpg

from .config import PTSettings


async def create_gateway_pool(settings: PTSettings) -> asyncpg.Pool:
    """Pool on gateway_db as `cctv_forwarder`: SELECT cctv_frame/dust_inspection,
    SELECT/DELETE/UPDATE(attempts) cctv_transfer_queue."""
    pool = await asyncpg.create_pool(
        dsn=settings.gw_dsn,
        min_size=settings.gw_db_pool_min,
        max_size=settings.gw_db_pool_max,
    )
    assert pool is not None
    return pool


async def create_decision_pool(settings: PTSettings) -> asyncpg.Pool:
    """Pool on decision_db (detector 롤 재사용): INSERT decision_record,
    SELECT classification_threshold.  배치 모드 생산자용."""
    pool = await asyncpg.create_pool(
        dsn=settings.decision_dsn,
        min_size=settings.decision_db_pool_min,
        max_size=settings.decision_db_pool_max,
    )
    assert pool is not None
    return pool
