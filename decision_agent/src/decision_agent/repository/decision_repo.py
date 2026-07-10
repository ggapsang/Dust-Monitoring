"""decision_record repository — fetch fully-arrived pending records,
write final_decision/decided_at/mapping_id."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import asyncpg


@dataclass(slots=True)
class PendingRecord:
    """A decision_record where all 3 channels have arrived but final_decision='pending'."""

    id: uuid.UUID
    station_id: str
    observation_timestamp: datetime
    anomaly_detection_result: str
    object_detection_result: str
    sensor_analysis_result: str


BrowseTab = Literal["recent", "pending", "stuck"]
_VALID_DECISION_LEVELS = ("normal", "caution", "warning")


class DecisionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch_pending(self, limit: int) -> list[PendingRecord]:
        """Return rows where every channel has reported and final_decision is pending.

        Ordered by observation_timestamp so older observations decide first.
        """
        rows = await self._pool.fetch(
            """
            SELECT id, station_id, observation_timestamp,
                   anomaly_detection_result::text AS anomaly_detection_result,
                   object_detection_result::text  AS object_detection_result,
                   sensor_analysis_result::text   AS sensor_analysis_result
              FROM decision_record
             WHERE final_decision = 'pending'
               AND anomaly_detection_result <> 'pending'
               AND object_detection_result  <> 'pending'
               AND sensor_analysis_result   <> 'pending'
             ORDER BY observation_timestamp ASC
             LIMIT $1
            """,
            limit,
        )
        return [
            PendingRecord(
                id=r["id"],
                station_id=r["station_id"],
                observation_timestamp=r["observation_timestamp"],
                anomaly_detection_result=r["anomaly_detection_result"],
                object_detection_result=r["object_detection_result"],
                sensor_analysis_result=r["sensor_analysis_result"],
            )
            for r in rows
        ]

    async def browse(
        self,
        tab: BrowseTab,
        page: int,
        page_size: int,
        stuck_after_sec: int,
    ) -> tuple[list[dict], int]:
        """Return (rows, total) for the admin browser. Rows are dicts with all
        columns the UI needs, including ::text-cast enums."""
        page = max(1, page)
        offset = (page - 1) * page_size

        if tab == "recent":
            where = "TRUE"
            params: list = []
            order = "ORDER BY COALESCE(decided_at, observation_timestamp) DESC"
        elif tab == "pending":
            where = "final_decision = 'pending'"
            params = []
            order = "ORDER BY observation_timestamp DESC"
        elif tab == "stuck":
            where = (
                "final_decision = 'pending' "
                "AND observation_timestamp < NOW() - ($1::int * INTERVAL '1 second')"
            )
            params = [stuck_after_sec]
            order = "ORDER BY observation_timestamp ASC"
        else:
            raise ValueError(f"unknown tab: {tab}")

        total_row = await self._pool.fetchrow(
            f"SELECT COUNT(*) AS n FROM decision_record WHERE {where}", *params
        )
        total = int(total_row["n"]) if total_row else 0

        rows = await self._pool.fetch(
            f"""
            SELECT id, station_id, dust_id, observation_timestamp,
                   anomaly_detection_result::text AS anomaly_detection_result,
                   object_detection_result::text  AS object_detection_result,
                   sensor_analysis_result::text   AS sensor_analysis_result,
                   final_decision::text           AS final_decision,
                   decided_at, mapping_id, sent_at, created_at
              FROM decision_record
             WHERE {where}
             {order}
             LIMIT {int(page_size)} OFFSET {int(offset)}
            """,
            *params,
        )
        return [dict(r) for r in rows], total

    async def force_decide(
        self,
        decision_id: uuid.UUID,
        final_decision: str,
        mapping_id: int | None,
    ) -> bool:
        """Manually set final_decision on a pending row (admin override)."""
        if final_decision not in _VALID_DECISION_LEVELS:
            raise ValueError(f"invalid final_decision: {final_decision}")
        result = await self._pool.execute(
            """
            UPDATE decision_record
               SET final_decision = $2::decision_result,
                   decided_at     = NOW(),
                   mapping_id     = $3
             WHERE id = $1
               AND final_decision = 'pending'
            """,
            decision_id,
            final_decision,
            mapping_id,
        )
        try:
            count = int(result.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            count = 0
        return count > 0

    async def status_counts(self, stuck_after_sec: int) -> dict[str, int]:
        """Return summary counts for the admin status bar."""
        row = await self._pool.fetchrow(
            """
            SELECT
              SUM(CASE WHEN final_decision = 'pending' THEN 1 ELSE 0 END)::int    AS pending,
              SUM(CASE WHEN decided_at >= NOW() - INTERVAL '1 hour' THEN 1 ELSE 0 END)::int
                 AS decided_last_hour,
              SUM(CASE WHEN final_decision = 'pending'
                        AND observation_timestamp < NOW() - ($1::int * INTERVAL '1 second')
                       THEN 1 ELSE 0 END)::int                                    AS stuck
              FROM decision_record
            """,
            stuck_after_sec,
        )
        if row is None:
            return {"pending": 0, "decided_last_hour": 0, "stuck": 0}
        return {
            "pending": row["pending"] or 0,
            "decided_last_hour": row["decided_last_hour"] or 0,
            "stuck": row["stuck"] or 0,
        }

    async def mark_decided(
        self,
        decision_id: uuid.UUID,
        final_decision: str,
        mapping_id: int,
    ) -> bool:
        """UPDATE the verdict columns. Returns True if a row was updated.

        Guarded by `final_decision = 'pending'` so concurrent agents don't
        clobber each other's writes.
        """
        result = await self._pool.execute(
            """
            UPDATE decision_record
               SET final_decision = $2::decision_result,
                   decided_at     = NOW(),
                   mapping_id     = $3
             WHERE id = $1
               AND final_decision = 'pending'
            """,
            decision_id,
            final_decision,
            mapping_id,
        )
        # asyncpg returns "UPDATE n" — split last token to get count
        try:
            count = int(result.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            count = 0
        return count > 0
