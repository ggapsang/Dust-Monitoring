"""Integration tests for DustInspectionRepository.

Requires a live PostgreSQL with the dust_inspection table.  The fixture
auto-skips if no DB is reachable, so this file is safe to run in CI
environments without docker.

DSN sources (first match wins):
  1. env var ``LOAS_TEST_DSN``  (full DSN string)
  2. fallback: ``postgresql://postgres:test@localhost:5433/gateway_db``
     which matches the throwaway container the PR validates against.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import asyncpg
import pytest
import pytest_asyncio

from gw_proto.codec.loas.constants import (
    DOID_DUST_INSPECTION,
    PROTOCOL_VERSION,
    SOP_DUST,
)
from gw_proto.codec.loas.dust_framing import DustHeader
from gw_proto.codec.loas.dust_xml import parse_dust_inspection

from ingestion_gateway.repository.dust_inspection_repo import (
    DustInspectionRepository,
)


DEFAULT_DSN = "postgresql://postgres:test@localhost:5433/gateway_db"


@pytest_asyncio.fixture
async def pool():
    dsn = os.environ.get("LOAS_TEST_DSN", DEFAULT_DSN)
    try:
        p = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"no test postgres at {dsn}: {exc}")
        return
    # Wipe between tests so row counts are deterministic.
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE cctv_frame, dust_inspection RESTART IDENTITY CASCADE")
    yield p
    await p.close()


SAMPLE_XML = b"""<ELEMENT>
    <CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>
    <DUST_DATA>0.0400</DUST_DATA>
    <DUST_ALARM>3</DUST_ALARM>
    <DATETIME>2026-05-23 18:30:00.000000</DATETIME>
    <SENSOR_TYPE>3</SENSOR_TYPE>
    <WAYPOINT_X>12.345</WAYPOINT_X>
    <WAYPOINT_Y>67.890</WAYPOINT_Y>
    <WAYPOINT_Z>0.000</WAYPOINT_Z>
    <UGV_ID>1</UGV_ID>
    <MISSION_ID>1734498123456</MISSION_ID>
    <ROT_W>1.000</ROT_W>
</ELEMENT>"""


def _header() -> DustHeader:
    return DustHeader(
        sop=SOP_DUST,
        data_object_id=DOID_DUST_INSPECTION,
        version=PROTOCOL_VERSION,
        encryption=0,
        timestamp=1_700_000_000,
        length=len(SAMPLE_XML),
    )


@pytest.mark.asyncio
async def test_insert_returns_id_and_persists_all_columns(pool):
    repo = DustInspectionRepository(pool)
    payload = parse_dust_inspection(SAMPLE_XML)

    row_id = await repo.insert(
        _header(), payload, raw_xml=SAMPLE_XML.decode("utf-8")
    )
    assert row_id is not None and row_id > 0

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM dust_inspection WHERE id = $1", row_id
        )
    assert row is not None
    assert row["cmd_id"] == "DUST_INSPECTION_INFOR"
    assert row["data_object_id"] == DOID_DUST_INSPECTION
    assert row["protocol_version"] == PROTOCOL_VERSION
    assert row["dust_value"] == pytest.approx(0.04)
    assert row["dust_alarm"] == 3
    assert row["sensor_type"] == 3
    assert row["waypoint_x"] == pytest.approx(12.345)
    assert row["waypoint_y"] == pytest.approx(67.890)
    assert row["rot_w"] == pytest.approx(1.0)
    assert row["ugv_id"] == 1
    assert row["mission_id"] == 1734498123456
    assert row["sensor_epoch_sec"] == 1_700_000_000
    assert row["sensor_datetime"] == datetime(
        2026, 5, 23, 18, 30, 0, tzinfo=timezone.utc
    )
    assert row["raw_xml"] is not None and "DUST_DATA" in row["raw_xml"]
    assert row["received_at"] is not None


@pytest.mark.asyncio
async def test_minimal_payload_inserts_with_nulls(pool):
    """Missing optional tags must land as NULL, not blow up."""
    minimal = b"<ELEMENT><CMD_ID>DUST_INSPECTION_INFOR</CMD_ID></ELEMENT>"
    repo = DustInspectionRepository(pool)
    payload = parse_dust_inspection(minimal)
    hdr = DustHeader(
        sop=SOP_DUST,
        data_object_id=DOID_DUST_INSPECTION,
        version=PROTOCOL_VERSION,
        encryption=0,
        timestamp=0,
        length=len(minimal),
    )

    row_id = await repo.insert(hdr, payload, raw_xml=minimal.decode())

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT dust_value, ugv_id, mission_id, sensor_datetime "
            "FROM dust_inspection WHERE id = $1",
            row_id,
        )
    assert row["dust_value"] is None
    assert row["ugv_id"] is None
    assert row["mission_id"] is None
    assert row["sensor_datetime"] is None


@pytest.mark.asyncio
async def test_multiple_inserts_get_distinct_ids(pool):
    repo = DustInspectionRepository(pool)
    payload = parse_dust_inspection(SAMPLE_XML)
    raw = SAMPLE_XML.decode("utf-8")
    ids = [
        await repo.insert(_header(), payload, raw_xml=raw)
        for _ in range(3)
    ]
    assert len(set(ids)) == 3
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT count(*) FROM dust_inspection")
    assert n == 3
