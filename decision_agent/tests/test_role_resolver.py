"""RoleResolver tests — DB-backed.

Skipped automatically when DA_TEST_DSN isn't set.
"""

from __future__ import annotations

import pytest

from decision_agent.role_resolver import RoleResolver


pytestmark = pytest.mark.asyncio


async def test_role_resolver_loads_initial_seed(pool) -> None:
    resolver = RoleResolver(pool, refresh_sec=3600)
    await resolver.refresh()

    columns = resolver.role_columns()
    assert columns == {
        "static_dust":  "anomaly_detection_result",
        "dynamic_dust": "object_detection_result",
        "iot_sensor":   "sensor_analysis_result",
    }


async def test_role_resolver_reflects_remap_after_refresh(pool) -> None:
    resolver = RoleResolver(pool, refresh_sec=3600)
    await resolver.refresh()

    # Swap static_dust to use object_detection (i.e. object_detection plays
    # both static and dynamic roles temporarily — overlap is fine for this test).
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE role_mapping
               SET component_name = 'object_detection',
                   updated_at = NOW()
             WHERE detection_role = 'static_dust'
            """
        )

    try:
        await resolver.refresh()
        assert resolver.column_for_role("static_dust") == "object_detection_result"
    finally:
        # Restore.
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE role_mapping
                   SET component_name = 'anomaly_detection',
                       updated_at = NOW()
                 WHERE detection_role = 'static_dust'
                """
            )
