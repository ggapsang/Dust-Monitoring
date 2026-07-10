"""Decision DB repository — 판정 완료·미전송 행 조회 + 전송표시(sent_at).

egress 는 egress_role 로 SELECT + UPDATE(sent_at)만 한다(단방향).
LOAS 적재에 필요한 것: id(키), dust_id(→ gateway_db 조인), final_decision(→ event_id),
image_b64(→ image_data).  나머지 24컬럼은 dust_inspection 에서 가져온다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import asyncpg


@dataclass(slots=True)
class DecisionRecord:
    """판정 완료된 decision_record 1행(LOAS 적재용 최소 필드)."""

    id: uuid.UUID
    dust_id: int | None
    final_decision: str
    image_b64: str | None


class DecisionRepository:
    """Read-pending and mark-sent. sent_at-only UPDATE."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch_pending(self, limit: int) -> list[DecisionRecord]:
        rows = await self._pool.fetch(
            """
            SELECT id, dust_id,
                   final_decision::text AS final_decision,
                   image_b64
              FROM decision_record
             WHERE final_decision <> 'pending'
               AND sent_at IS NULL
             ORDER BY decided_at
             LIMIT $1
            """,
            limit,
        )
        return [
            DecisionRecord(
                id=r["id"],
                dust_id=r["dust_id"],
                final_decision=r["final_decision"],
                image_b64=r["image_b64"],
            )
            for r in rows
        ]

    async def mark_sent(self, decision_id: uuid.UUID) -> None:
        await self._pool.execute(
            "UPDATE decision_record SET sent_at = NOW() WHERE id = $1 AND sent_at IS NULL",
            decision_id,
        )
