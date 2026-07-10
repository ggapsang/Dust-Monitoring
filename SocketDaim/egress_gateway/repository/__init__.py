"""asyncpg-based repository for the decision DB."""

from __future__ import annotations

import asyncpg

from ..config import EgressSettings


async def create_pool(settings: EgressSettings) -> asyncpg.Pool:
    """decision_db pool (egress_role) — 판정 소스."""
    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    assert pool is not None
    return pool


async def create_gateway_pool(settings: EgressSettings) -> asyncpg.Pool:
    """gateway_db pool (gw_reader) — dust_inspection 24컬럼 읽기."""
    pool = await asyncpg.create_pool(
        dsn=settings.gw_dsn,
        min_size=settings.gw_db_pool_min,
        max_size=settings.gw_db_pool_max,
    )
    assert pool is not None
    return pool


from .decision_repo import DecisionRecord, DecisionRepository  # noqa: E402
from .gateway_repo import GatewayRepository  # noqa: E402

__all__ = [
    "create_pool",
    "create_gateway_pool",
    "DecisionRecord",
    "DecisionRepository",
    "GatewayRepository",
]
