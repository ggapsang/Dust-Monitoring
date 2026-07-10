"""Admin HTTP API tests.

Use httpx.AsyncClient with FastAPI's ASGI transport so requests run on
the same event loop as the asyncpg pool (TestClient spawns its own loop
which conflicts with asyncpg connections).

Requires DA_TEST_DSN.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest

from decision_agent.admin import build_app
from decision_agent.config import DASettings
from decision_agent.judge import Judge
from decision_agent.repository import DecisionRepository
from decision_agent.role_resolver import RoleResolver

pytestmark = pytest.mark.asyncio


async def _client(test_dsn: str):
    pool = await asyncpg.create_pool(dsn=test_dsn, min_size=1, max_size=4)
    assert pool is not None
    settings = DASettings(
        db_host="ignored", db_user="ignored", db_password="ignored", db_name="ignored"
    )
    repo = DecisionRepository(pool)
    judge = Judge(pool)
    await judge.load()
    resolver = RoleResolver(pool, refresh_sec=3600)
    await resolver.refresh()
    app = build_app(settings, pool, repo, judge, resolver)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return client, pool, judge, resolver


async def test_index_renders_html(test_dsn: str) -> None:
    client, pool, *_ = await _client(test_dsn)
    try:
        r = await client.get("/")
        assert r.status_code == 200
        assert "Decision Agent Admin" in r.text
        assert "/admin/static/css/admin.css" in r.text
    finally:
        await client.aclose()
        await pool.close()


async def test_status_returns_counts(test_dsn: str) -> None:
    client, pool, *_ = await _client(test_dsn)
    try:
        r = await client.get("/admin/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["db_ok"] is True
        for k in ("pending", "decided_last_hour", "stuck"):
            assert k in data and isinstance(data[k], int)
        assert data["alarm_mapping_loaded_at"]
        assert data["role_mapping_loaded_at"]
    finally:
        await client.aclose()
        await pool.close()


async def test_role_mapping_get_and_patch(test_dsn: str) -> None:
    client, pool, _, resolver = await _client(test_dsn)
    try:
        r = await client.get("/admin/api/role-mapping")
        assert r.status_code == 200
        initial = {row["detection_role"]: row["component_name"] for row in r.json()}
        assert initial["static_dust"] == "anomaly_detection"

        try:
            r = await client.patch(
                "/admin/api/role-mapping/static_dust",
                json={"component_name": "object_detection"},
            )
            assert r.status_code == 200
            assert r.json()["ok"] is True
            assert resolver.column_for_role("static_dust") == "object_detection_result"

            r = await client.patch(
                "/admin/api/role-mapping/static_dust",
                json={"component_name": "garbage"},
            )
            assert r.status_code == 400
        finally:
            await client.patch(
                "/admin/api/role-mapping/static_dust",
                json={"component_name": "anomaly_detection"},
            )
    finally:
        await client.aclose()
        await pool.close()


async def test_alarm_mapping_get_and_patch(test_dsn: str) -> None:
    client, pool, judge, _ = await _client(test_dsn)
    try:
        r = await client.get("/admin/api/alarm-mapping")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 8

        target = next(x for x in rows if x["final_decision"] == "normal")
        tid = target["id"]
        try:
            r = await client.patch(
                f"/admin/api/alarm-mapping/{tid}", json={"final_decision": "warning"}
            )
            assert r.status_code == 200
            f, _id = judge.judge(
                target["iot_sensor_level"],
                target["static_model_result"],
                target["dynamic_model_result"],
            )
            assert f == "warning"

            r = await client.patch(
                f"/admin/api/alarm-mapping/{tid}", json={"final_decision": "garbage"}
            )
            assert r.status_code == 422
        finally:
            await client.patch(
                f"/admin/api/alarm-mapping/{tid}",
                json={"final_decision": target["final_decision"]},
            )
    finally:
        await client.aclose()
        await pool.close()


async def test_decisions_recent_pending_stuck(
    test_dsn: str, clean_decision_table
) -> None:
    client, pool, *_ = await _client(test_dsn)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO decision_record (
                    id, station_id, observation_timestamp,
                    anomaly_detection_result, object_detection_result, sensor_analysis_result,
                    final_decision, decided_at
                ) VALUES
                  ($1, 'ST-DEC',   NOW(),                       'normal',  'normal', 'normal', 'normal',  NOW()),
                  ($2, 'ST-PEND',  NOW(),                       'pending', 'normal', 'normal', 'pending', NULL),
                  ($3, 'ST-STUCK', NOW() - INTERVAL '10 minutes','pending','pending','pending','pending', NULL)
                """,
                uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
            )

        recent = (await client.get("/admin/api/decisions?tab=recent")).json()
        assert recent["total"] >= 3
        assert recent["tab"] == "recent"

        pending = (await client.get("/admin/api/decisions?tab=pending")).json()
        stations = {r["station_id"] for r in pending["rows"]}
        assert {"ST-PEND", "ST-STUCK"}.issubset(stations)

        stuck = (await client.get("/admin/api/decisions?tab=stuck")).json()
        stuck_stations = {r["station_id"] for r in stuck["rows"]}
        assert "ST-STUCK" in stuck_stations
        assert "ST-PEND" not in stuck_stations
    finally:
        await client.aclose()
        await pool.close()


async def test_force_decide(test_dsn: str, clean_decision_table) -> None:
    client, pool, *_ = await _client(test_dsn)
    try:
        rid = uuid.uuid4()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO decision_record (
                    id, station_id, observation_timestamp,
                    anomaly_detection_result, object_detection_result, sensor_analysis_result,
                    final_decision
                ) VALUES ($1, 'ST-FORCE', NOW() - INTERVAL '20 minutes',
                         'normal', 'pending', 'normal', 'pending')
                """,
                rid,
            )

        r = await client.post(
            f"/admin/api/decisions/{rid}/force", json={"final_decision": "caution"}
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

        r2 = await client.post(
            f"/admin/api/decisions/{rid}/force", json={"final_decision": "warning"}
        )
        assert r2.status_code == 409

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT final_decision::text AS f, decided_at FROM decision_record WHERE id = $1",
                rid,
            )
        assert row["f"] == "caution"
        assert row["decided_at"] is not None
    finally:
        await client.aclose()
        await pool.close()


async def test_reload_endpoints(test_dsn: str) -> None:
    client, pool, *_ = await _client(test_dsn)
    try:
        r1 = await client.post("/admin/api/reload/role-mapping")
        assert r1.status_code == 200
        assert r1.json()["ok"] is True
        assert r1.json()["loaded_at"]

        r2 = await client.post("/admin/api/reload/alarm-mapping")
        assert r2.status_code == 200
        assert r2.json()["ok"] is True
        assert r2.json()["loaded_at"]
    finally:
        await client.aclose()
        await pool.close()
