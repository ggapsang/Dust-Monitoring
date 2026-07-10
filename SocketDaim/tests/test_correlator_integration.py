"""Integration tests for FrameCorrelator against a real postgres.

Auto-skips when no DB is reachable.  Each test seeds rows with explicit
``received_at`` values so the pairing SQL is exercised against
deterministic timestamps instead of ``clock_timestamp()``.

DSN sources (first match wins):
  1. env var ``LOAS_TEST_DSN``
  2. fallback: ``postgresql://postgres:test@localhost:5433/gateway_db``
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from ingestion_gateway.correlator import FrameCorrelator


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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _insert_dust(pool: asyncpg.Pool, *, at: datetime) -> int:
    return await pool.fetchval(
        """
        INSERT INTO dust_inspection (received_at, cmd_id)
        VALUES ($1, 'DUST_INSPECTION_INFOR')
        RETURNING id
        """,
        at,
    )


async def _insert_frame(pool: asyncpg.Pool, *, at: datetime) -> int:
    return await pool.fetchval(
        """
        INSERT INTO cctv_frame
          (received_at, amr_id, resolution, file_path, byte_size)
        VALUES ($1, 'amr-01', 'V1080', '/x.jpg', 1)
        RETURNING id
        """,
        at,
    )


async def _pair_status(pool: asyncpg.Pool, frame_id: int) -> tuple[int | None, datetime | None]:
    row = await pool.fetchrow(
        "SELECT dust_inspection_id, paired_at FROM cctv_frame WHERE id = $1",
        frame_id,
    )
    return row["dust_inspection_id"], row["paired_at"]


# Anchor "now" inside the lookback window for every test
NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_frames_inside_window_get_paired(pool):
    dust_id = await _insert_dust(pool, at=NOW)
    inside1 = await _insert_frame(pool, at=NOW - timedelta(seconds=1))
    inside2 = await _insert_frame(pool, at=NOW + timedelta(seconds=1))

    c = FrameCorrelator(pool, before_sec=2.0, after_sec=2.0, lookback_sec=600.0)
    paired = await c.tick()

    assert paired == 2
    for fid in (inside1, inside2):
        di, paired_at = await _pair_status(pool, fid)
        assert di == dust_id
        assert paired_at is not None


@pytest.mark.asyncio
async def test_frames_outside_window_stay_orphan(pool):
    await _insert_dust(pool, at=NOW)
    far_before = await _insert_frame(pool, at=NOW - timedelta(seconds=10))
    far_after = await _insert_frame(pool, at=NOW + timedelta(seconds=10))

    c = FrameCorrelator(pool, before_sec=2.0, after_sec=2.0, lookback_sec=600.0)
    paired = await c.tick()

    assert paired == 0
    for fid in (far_before, far_after):
        di, _ = await _pair_status(pool, fid)
        assert di is None


@pytest.mark.asyncio
async def test_frame_between_two_dust_events_picks_nearest(pool):
    early = await _insert_dust(pool, at=NOW - timedelta(seconds=1))
    late = await _insert_dust(pool, at=NOW + timedelta(seconds=1))
    # frame is 0.2s closer to `late`
    frame_id = await _insert_frame(pool, at=NOW + timedelta(milliseconds=200))

    c = FrameCorrelator(pool, before_sec=3.0, after_sec=3.0, lookback_sec=600.0)
    paired = await c.tick()

    assert paired == 1
    di, _ = await _pair_status(pool, frame_id)
    assert di == late
    # Defensive: ensure we picked the correct one, not the other
    assert di != early


@pytest.mark.asyncio
async def test_already_paired_frame_is_not_touched(pool):
    dust_id = await _insert_dust(pool, at=NOW)
    frame_id = await _insert_frame(pool, at=NOW)

    # First pass pairs it.
    c = FrameCorrelator(pool, before_sec=2.0, after_sec=2.0, lookback_sec=600.0)
    assert await c.tick() == 1
    _, paired_at_1 = await _pair_status(pool, frame_id)
    assert paired_at_1 is not None

    # Second pass must not touch the row (count = 0, paired_at unchanged).
    assert await c.tick() == 0
    _, paired_at_2 = await _pair_status(pool, frame_id)
    assert paired_at_2 == paired_at_1


@pytest.mark.asyncio
async def test_lookback_skips_old_frames(pool):
    """A frame older than `lookback_sec` is not considered, even if a
    dust event in-window exists."""
    old_dust = await _insert_dust(pool, at=NOW - timedelta(minutes=15))
    old_frame = await _insert_frame(pool, at=NOW - timedelta(minutes=15))

    c = FrameCorrelator(pool, before_sec=2.0, after_sec=2.0, lookback_sec=600.0)
    paired = await c.tick()

    assert paired == 0
    di, _ = await _pair_status(pool, old_frame)
    assert di is None


@pytest.mark.asyncio
async def test_one_dust_pairs_with_many_frames(pool):
    """Realistic case: 1 dust event at a waypoint, several frames within
    a few seconds of it.  All should pair to that single inspection."""
    dust_id = await _insert_dust(pool, at=NOW)
    frame_ids = [
        await _insert_frame(pool, at=NOW + timedelta(milliseconds=offset_ms))
        for offset_ms in (-1500, -500, 0, 500, 1500)
    ]

    c = FrameCorrelator(pool, before_sec=2.0, after_sec=2.0, lookback_sec=600.0)
    assert await c.tick() == 5

    for fid in frame_ids:
        di, _ = await _pair_status(pool, fid)
        assert di == dust_id


@pytest.mark.asyncio
async def test_asymmetric_window(pool):
    """before_sec/after_sec are independent — a tight before, loose after
    must reflect in pairing."""
    dust_id = await _insert_dust(pool, at=NOW)
    too_early = await _insert_frame(pool, at=NOW - timedelta(seconds=3))
    after_ok = await _insert_frame(pool, at=NOW + timedelta(seconds=3))

    c = FrameCorrelator(
        pool, before_sec=1.0, after_sec=5.0, lookback_sec=600.0
    )
    assert await c.tick() == 1

    di_early, _ = await _pair_status(pool, too_early)
    di_after, _ = await _pair_status(pool, after_ok)
    assert di_early is None
    assert di_after == dust_id
