"""End-to-end test: insert decision_record rows, run one polling tick,
verify final_decision/decided_at/mapping_id are written correctly.

Requires a live decision_db with init_db.sql + seed_mapping.sql applied.
DA_TEST_DSN must be a connection string with privileges to TRUNCATE
decision_record and INSERT/UPDATE all columns (i.e. owner / superuser).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from decision_agent.judge import Judge
from decision_agent.poller import Poller
from decision_agent.repository import DecisionRepository
from decision_agent.role_resolver import RoleResolver

pytestmark = pytest.mark.asyncio


# Same 8 cases as test_judge.py (2×2×2), but here we additionally verify the
# correct value lands in the DB after a polling tick.  센서 2단계 + 위험(danger).
SCENARIOS: list[tuple[str, str, str, str, str, str, str]] = [
    # (sensor_level (sensor channel value), static_result (anomaly channel),
    #  dynamic_result (object channel), expected_final, ...labels for log)
    ("normal",   "normal",   "normal",   "normal",  "n", "n", "n"),
    ("normal",   "normal",   "abnormal", "caution", "n", "n", "a"),
    ("normal",   "abnormal", "normal",   "caution", "n", "a", "n"),
    ("normal",   "abnormal", "abnormal", "caution", "n", "a", "a"),
    ("abnormal", "normal",   "normal",   "warning", "a", "n", "n"),
    ("abnormal", "normal",   "abnormal", "danger",  "a", "n", "a"),
    ("abnormal", "abnormal", "normal",   "danger",  "a", "a", "n"),
    ("abnormal", "abnormal", "abnormal", "danger",  "a", "a", "a"),
]


async def _insert_scenario(
    pool, iot: str, static_: str, dynamic_: str, station_id: str
) -> uuid.UUID:
    """Insert a fully-arrived pending record. Returns the new id."""
    rec_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO decision_record (
                id, station_id, observation_timestamp,
                anomaly_detection_result, anomaly_detection_at,
                object_detection_result,  object_detection_at,
                sensor_analysis_result,   sensor_analysis_at,
                final_decision
            ) VALUES (
                $1, $2, NOW(),
                $3::channel_result, NOW(),
                $4::channel_result, NOW(),
                $5::channel_result, NOW(),
                'pending'
            )
            """,
            rec_id,
            station_id,
            static_,
            dynamic_,
            iot,
        )
    return rec_id


async def test_all_8_scenarios_resolve_correctly(pool, clean_decision_table) -> None:
    # Seed all 8 records.
    inserted: list[tuple[uuid.UUID, str]] = []  # (id, expected_final)
    for i, (iot, static_, dynamic_, expected, *_) in enumerate(SCENARIOS):
        sid = f"ST-{i:03d}"
        rid = await _insert_scenario(pool, iot, static_, dynamic_, sid)
        inserted.append((rid, expected))

    # Build the agent's components against the same pool.
    repo = DecisionRepository(pool)
    judge = Judge(pool)
    await judge.load()
    resolver = RoleResolver(pool, refresh_sec=3600)
    await resolver.refresh()

    poller = Poller(repo, judge, resolver, interval_sec=0.0, batch_size=100)
    stop = asyncio.Event()
    # One tick is enough.
    await poller._tick()  # noqa: SLF001 — invoke a single iteration

    # Verify each row.
    async with pool.acquire() as conn:
        for rid, expected in inserted:
            row = await conn.fetchrow(
                """
                SELECT final_decision::text AS final_decision,
                       decided_at,
                       mapping_id
                  FROM decision_record
                 WHERE id = $1
                """,
                rid,
            )
            assert row is not None, f"row {rid} disappeared"
            assert row["final_decision"] == expected, (
                f"id={rid}: got {row['final_decision']}, expected {expected}"
            )
            assert row["decided_at"] is not None
            assert row["mapping_id"] is not None


async def test_partial_arrival_skipped(pool, clean_decision_table) -> None:
    """A row with one channel still 'pending' must NOT be decided."""
    rec_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO decision_record (
                id, station_id, observation_timestamp,
                anomaly_detection_result,
                object_detection_result,
                sensor_analysis_result,
                final_decision
            ) VALUES (
                $1, 'ST-PARTIAL', NOW(),
                'normal'::channel_result,
                'pending'::channel_result,    -- not yet arrived
                'normal'::channel_result,
                'pending'
            )
            """,
            rec_id,
        )

    repo = DecisionRepository(pool)
    judge = Judge(pool)
    await judge.load()
    resolver = RoleResolver(pool, refresh_sec=3600)
    await resolver.refresh()

    poller = Poller(repo, judge, resolver, interval_sec=0.0, batch_size=100)
    await poller._tick()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT final_decision::text AS f, decided_at FROM decision_record WHERE id = $1",
            rec_id,
        )
    assert row is not None
    assert row["f"] == "pending"
    assert row["decided_at"] is None


async def test_idempotent_decide_does_not_clobber(pool, clean_decision_table) -> None:
    """Running tick twice must not change a row already decided."""
    rid = await _insert_scenario(pool, "abnormal", "abnormal", "abnormal", "ST-IDEMP")

    repo = DecisionRepository(pool)
    judge = Judge(pool)
    await judge.load()
    resolver = RoleResolver(pool, refresh_sec=3600)
    await resolver.refresh()

    poller = Poller(repo, judge, resolver, interval_sec=0.0, batch_size=100)
    await poller._tick()

    async with pool.acquire() as conn:
        first = await conn.fetchrow(
            "SELECT final_decision::text AS f, decided_at FROM decision_record WHERE id = $1",
            rid,
        )
    assert first is not None
    assert first["f"] == "danger"
    first_decided_at = first["decided_at"]

    # Second tick should be a no-op for this row.
    await poller._tick()
    async with pool.acquire() as conn:
        second = await conn.fetchrow(
            "SELECT final_decision::text AS f, decided_at FROM decision_record WHERE id = $1",
            rid,
        )
    assert second is not None
    assert second["decided_at"] == first_decided_at, "row should not be re-decided"
