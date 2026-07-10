"""Polling loop — fetch fully-arrived pending records, judge, mark decided."""

from __future__ import annotations

import asyncio

import structlog

from .judge import Judge, JudgeLookupError
from .repository import DecisionRepository, PendingRecord
from .role_resolver import RoleResolver

logger = structlog.get_logger(__name__)

_RECORD_FIELD: dict[str, str] = {
    "anomaly_detection_result": "anomaly_detection_result",
    "object_detection_result":  "object_detection_result",
    "sensor_analysis_result":   "sensor_analysis_result",
}


class Poller:
    def __init__(
        self,
        repo: DecisionRepository,
        judge: Judge,
        role_resolver: RoleResolver,
        *,
        interval_sec: float,
        batch_size: int,
    ) -> None:
        self._repo = repo
        self._judge = judge
        self._roles = role_resolver
        self._interval = interval_sec
        self._batch = batch_size

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info(
            "poller_started",
            interval_sec=self._interval,
            batch_size=self._batch,
        )
        while not stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("poller_tick_error")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass
        logger.info("poller_stopped")

    async def _tick(self) -> None:
        records = await self._repo.fetch_pending(self._batch)
        if not records:
            return
        logger.debug("poller_tick", pending=len(records))

        # Snapshot role->column once per tick.
        try:
            role_columns = self._roles.role_columns()
        except KeyError as exc:
            logger.error("role_mapping_unresolved", reason=str(exc))
            return

        for rec in records:
            await self._decide(rec, role_columns)

    async def _decide(
        self,
        rec: PendingRecord,
        role_columns: dict[str, str],
    ) -> None:
        try:
            iot_col     = role_columns["iot_sensor"]
            static_col  = role_columns["static_dust"]
            dynamic_col = role_columns["dynamic_dust"]
        except KeyError as exc:
            logger.error("role_columns_missing", id=str(rec.id), missing=str(exc))
            return

        try:
            iot_value     = getattr(rec, _RECORD_FIELD[iot_col])
            static_value  = getattr(rec, _RECORD_FIELD[static_col])
            dynamic_value = getattr(rec, _RECORD_FIELD[dynamic_col])
        except (AttributeError, KeyError) as exc:
            logger.error(
                "record_field_missing",
                id=str(rec.id),
                reason=str(exc),
                role_columns=role_columns,
            )
            return

        try:
            final_decision, mapping_id = self._judge.judge(
                iot_value, static_value, dynamic_value
            )
        except JudgeLookupError as exc:
            logger.error(
                "alarm_mapping_miss",
                id=str(rec.id),
                iot=iot_value,
                static=static_value,
                dynamic=dynamic_value,
                reason=str(exc),
            )
            return

        updated = await self._repo.mark_decided(rec.id, final_decision, mapping_id)
        if updated:
            logger.info(
                "decision_decided",
                id=str(rec.id),
                station_id=rec.station_id,
                final=final_decision,
                mapping_id=mapping_id,
            )
        else:
            # Already decided by another agent or row gone — safe to skip.
            logger.debug("decision_already_decided", id=str(rec.id))
