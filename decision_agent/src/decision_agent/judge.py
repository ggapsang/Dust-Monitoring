"""alarm_mapping cache + judge function.

Loads the 12-row truth table once at startup and exposes a pure lookup:
    (iot_level, static_result, dynamic_result) -> (final_decision, mapping_id)
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


class JudgeLookupError(KeyError):
    """Raised when a (iot, static, dynamic) tuple has no alarm_mapping row."""


class Judge:
    VALID_FINAL_LEVELS = ("normal", "caution", "warning", "danger")

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        # (iot_sensor_level, static_model_result, dynamic_model_result)
        #   -> (final_decision, mapping_id)
        self._table: dict[tuple[str, str, str], tuple[str, int]] = {}
        self._loaded_at: datetime | None = None

    async def load(self) -> None:
        rows = await self._pool.fetch(
            """
            SELECT id,
                   iot_sensor_level::text     AS iot_sensor_level,
                   static_model_result::text  AS static_model_result,
                   dynamic_model_result::text AS dynamic_model_result,
                   final_decision::text       AS final_decision
              FROM alarm_mapping
            """
        )
        self._table = {
            (
                r["iot_sensor_level"],
                r["static_model_result"],
                r["dynamic_model_result"],
            ): (r["final_decision"], r["id"])
            for r in rows
        }
        self._loaded_at = datetime.now(timezone.utc)
        logger.info("alarm_mapping_loaded", rows=len(self._table))
        if len(self._table) != 8:
            logger.warning(
                "alarm_mapping_unexpected_size",
                expected=8,  # 2(sensor) × 2(static) × 2(dynamic)
                actual=len(self._table),
            )

    @property
    def loaded_at(self) -> datetime | None:
        return self._loaded_at

    async def list_rows(self) -> list[dict]:
        rows = await self._pool.fetch(
            """
            SELECT id,
                   iot_sensor_level::text     AS iot_sensor_level,
                   static_model_result::text  AS static_model_result,
                   dynamic_model_result::text AS dynamic_model_result,
                   final_decision::text       AS final_decision,
                   description
              FROM alarm_mapping
             ORDER BY id
            """
        )
        return [dict(r) for r in rows]

    async def update_final(self, mapping_id: int, final_decision: str) -> bool:
        if final_decision not in self.VALID_FINAL_LEVELS:
            raise ValueError(f"invalid final_decision: {final_decision}")
        result = await self._pool.execute(
            "UPDATE alarm_mapping SET final_decision = $2::final_level WHERE id = $1",
            mapping_id,
            final_decision,
        )
        try:
            count = int(result.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            count = 0
        if count > 0:
            await self.load()
        return count > 0

    def judge(
        self,
        iot_level: str,
        static_result: str,
        dynamic_result: str,
    ) -> tuple[str, int]:
        """Return (final_decision, mapping_id) for the given 3-tuple."""
        key = (iot_level, static_result, dynamic_result)
        try:
            return self._table[key]
        except KeyError as exc:
            raise JudgeLookupError(
                f"no alarm_mapping for {key}"
            ) from exc
