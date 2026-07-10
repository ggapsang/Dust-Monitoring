"""role_mapping cache.

Maps `detection_role` (static_dust / dynamic_dust / iot_sensor) to the
`component_name` (anomaly_detection / object_detection / sensor_analysis)
currently assigned to that role. The poller uses the inverse — given a
component, decide which role's column to read it from.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


# The 3 detection roles a component can be assigned to.
ROLES = ("static_dust", "dynamic_dust", "iot_sensor")

# component_name -> decision_record column suffix (the result column).
_COMPONENT_TO_COLUMN: dict[str, str] = {
    "anomaly_detection": "anomaly_detection_result",
    "object_detection":  "object_detection_result",
    "sensor_analysis":   "sensor_analysis_result",
}


class RoleResolver:
    """Caches role_mapping rows; refreshed periodically."""

    def __init__(self, pool: asyncpg.Pool, refresh_sec: float) -> None:
        self._pool = pool
        self._refresh_sec = refresh_sec
        self._lock = asyncio.Lock()
        # detection_role -> component_name
        self._role_to_component: dict[str, str] = {}
        self._loaded_at: datetime | None = None
        self._refresh_task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.refresh()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except (asyncio.CancelledError, Exception):
                pass
            self._refresh_task = None

    async def refresh(self) -> None:
        rows = await self._pool.fetch(
            "SELECT detection_role, component_name FROM role_mapping"
        )
        async with self._lock:
            self._role_to_component = {
                r["detection_role"]: r["component_name"] for r in rows
            }
            self._loaded_at = datetime.now(timezone.utc)
        missing = [r for r in ROLES if r not in self._role_to_component]
        if missing:
            logger.warning("role_mapping_incomplete", missing=missing)
        logger.info(
            "role_mapping_loaded",
            mapping=dict(self._role_to_component),
        )

    @property
    def loaded_at(self) -> datetime | None:
        return self._loaded_at

    async def list_rows(self) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT detection_role, component_name, description, updated_at "
            "FROM role_mapping ORDER BY id"
        )
        return [dict(r) for r in rows]

    async def update_component(self, detection_role: str, component_name: str) -> bool:
        if component_name not in _COMPONENT_TO_COLUMN:
            raise ValueError(f"unknown component_name: {component_name}")
        result = await self._pool.execute(
            "UPDATE role_mapping SET component_name = $2, updated_at = NOW() "
            "WHERE detection_role = $1",
            detection_role,
            component_name,
        )
        try:
            count = int(result.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            count = 0
        if count > 0:
            await self.refresh()
        return count > 0

    def column_for_role(self, role: str) -> str:
        """Return the decision_record column that holds the result for `role`.

        Raises KeyError if role isn't mapped or component name unknown.
        """
        component = self._role_to_component[role]
        return _COMPONENT_TO_COLUMN[component]

    def role_columns(self) -> dict[str, str]:
        """Snapshot: {role -> column} for the 3 standard roles."""
        return {role: self.column_for_role(role) for role in ROLES}

    async def _refresh_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._refresh_sec)
                try:
                    await self.refresh()
                except Exception:
                    logger.exception("role_mapping_refresh_error")
        except asyncio.CancelledError:
            return
