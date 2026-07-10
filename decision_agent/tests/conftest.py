"""Shared test fixtures.

Integration tests need a real Postgres. Set DA_TEST_DSN to point at an
already-running database that has init_db.sql + seed_mapping.sql applied
(e.g. SocketDaim's `postgres-decision` exposed on host port 2346, or a
fresh container started by the developer).

If DA_TEST_DSN is unset, integration tests are skipped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make src/ importable without installing.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import asyncpg  # noqa: E402


def _test_dsn() -> str | None:
    return os.environ.get("DA_TEST_DSN")


@pytest.fixture(scope="session")
def test_dsn() -> str:
    dsn = _test_dsn()
    if not dsn:
        pytest.skip("DA_TEST_DSN not set; skipping integration test")
    return dsn


@pytest.fixture
async def pool(test_dsn: str):
    pool = await asyncpg.create_pool(dsn=test_dsn, min_size=1, max_size=4)
    assert pool is not None
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def clean_decision_table(pool):
    """Truncate decision_record before each test."""
    await pool.execute("TRUNCATE decision_record")
    yield
    await pool.execute("TRUNCATE decision_record")
