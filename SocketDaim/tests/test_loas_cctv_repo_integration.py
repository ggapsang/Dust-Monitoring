"""Integration tests for CctvFrameRepository.

Mirrors the LOAS DUST repo integration test pattern — auto-skip if no DB
is reachable, TRUNCATE between tests, real INSERTs into cctv_frame.

DSN sources (first match wins):
  1. env var ``LOAS_TEST_DSN``
  2. fallback: ``postgresql://postgres:test@localhost:5433/gateway_db``
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import asyncpg
import pytest
import pytest_asyncio

from ingestion_gateway.repository.cctv_frame_repo import CctvFrameRepository


DEFAULT_DSN = "postgresql://postgres:test@localhost:5433/gateway_db"


@pytest_asyncio.fixture
async def pool():
    dsn = os.environ.get("LOAS_TEST_DSN", DEFAULT_DSN)
    try:
        p = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"no test postgres at {dsn}: {exc}")
        return
    async with p.acquire() as conn:
        await conn.execute(
            "TRUNCATE cctv_frame, dust_inspection RESTART IDENTITY CASCADE"
        )
    yield p
    await p.close()


@pytest.mark.asyncio
async def test_insert_returns_id_and_persists_columns(pool):
    repo = CctvFrameRepository(pool)
    ts = datetime(2026, 5, 26, 10, 30, 0, 123456, tzinfo=timezone.utc)

    row_id = await repo.insert(
        amr_id="amr-01",
        source_ip="10.0.0.42",
        resolution="V1080",
        file_path="/data/storage/cctv/amr-01/2026-05-26/10/123_V1080.jpg",
        byte_size=350_000,
        received_at=ts,
    )
    assert row_id is not None and row_id > 0

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM cctv_frame WHERE id = $1", row_id
        )
    assert row["amr_id"] == "amr-01"
    # asyncpg returns the inet type as a string-ish object
    assert str(row["source_ip"]) == "10.0.0.42"
    assert row["resolution"] == "V1080"
    assert row["byte_size"] == 350_000
    assert row["received_at"] == ts
    assert row["dust_inspection_id"] is None
    assert row["paired_at"] is None


@pytest.mark.asyncio
async def test_insert_with_null_source_ip(pool):
    """source_ip is nullable — handler may pass None for non-IP peers."""
    repo = CctvFrameRepository(pool)
    row_id = await repo.insert(
        amr_id="amr-01",
        source_ip=None,
        resolution="V720p",
        file_path="/x.jpg",
        byte_size=1,
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT source_ip, received_at FROM cctv_frame WHERE id = $1",
            row_id,
        )
    assert row["source_ip"] is None
    # received_at must have been filled by clock_timestamp()
    assert row["received_at"] is not None


@pytest.mark.asyncio
async def test_explicit_received_at_overrides_default(pool):
    repo = CctvFrameRepository(pool)
    fixed = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    row_id = await repo.insert(
        amr_id="amr-01",
        source_ip=None,
        resolution="V640p",
        file_path="/x.jpg",
        byte_size=1,
        received_at=fixed,
    )
    async with pool.acquire() as conn:
        got = await conn.fetchval(
            "SELECT received_at FROM cctv_frame WHERE id = $1", row_id
        )
    assert got == fixed


@pytest.mark.asyncio
async def test_many_inserts_get_distinct_ids(pool):
    repo = CctvFrameRepository(pool)
    ids = [
        await repo.insert(
            amr_id="amr-01", source_ip=None, resolution="V1080",
            file_path=f"/x{i}.jpg", byte_size=i,
        )
        for i in range(1, 11)
    ]
    assert len(set(ids)) == 10
