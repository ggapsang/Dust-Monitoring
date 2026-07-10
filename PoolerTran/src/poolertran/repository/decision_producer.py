"""decision_db 생산자 — 배치 REST 결과를 decision_record 로 적재.

PoolerTran 이 waypoint 배치를 처리하면(REST 결과 = 정적/동적 2쌍), dust_value 와
두 score 를 임계(classification_threshold)로 2단계(normal/abnormal) 분류해
decision_record 1행을 INSERT 한다.  final_decision 은 'pending' 으로 남고
decision_agent 가 판정한다.

연결: decision_db (detector 롤 재사용).  멱등: dust_id UNIQUE → ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg


_INSERT_DECISION = """
INSERT INTO decision_record
    (station_id, observation_timestamp, dust_id,
     sensor_analysis_result,   sensor_analysis_at,
     anomaly_detection_result, anomaly_detection_at,
     object_detection_result,  object_detection_at,
     result_payload, image_b64)
VALUES ($1, $2, $3,
        $4::channel_result, now(),
        $5::channel_result, now(),
        $6::channel_result, now(),
        $7::jsonb, $8)
ON CONFLICT (dust_id) DO NOTHING
"""

_SELECT_THRESHOLDS = "SELECT key, threshold FROM classification_threshold"

_UPSERT_DLQ = """
INSERT INTO transfer_dlq (frame_id, attempts, last_error, source_row)
VALUES ($1, $2, $3, $4::jsonb)
ON CONFLICT (frame_id) DO UPDATE
    SET attempts         = EXCLUDED.attempts,
        last_error       = EXCLUDED.last_error,
        source_row       = EXCLUDED.source_row,
        dead_lettered_at = now()
"""


class DecisionProducer:
    """decision_record INSERT + 분류 임계 조회."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch_thresholds(self) -> dict[str, float]:
        """{'dust':.., 'static':.., 'dynamic':..} — admin UI 로 변경되는 값."""
        rows = await self._pool.fetch(_SELECT_THRESHOLDS)
        return {r["key"]: float(r["threshold"]) for r in rows}

    async def insert_decision(
        self,
        *,
        station_id: str,
        observation_timestamp: datetime,
        dust_id: int | None,
        sensor_result: str,
        anomaly_result: str,
        object_result: str,
        result_payload: Any,
        image_b64: str | None = None,
    ) -> bool:
        """decision_record 1행 INSERT(멱등).  새로 들어가면 True, 충돌(중복)이면 False."""
        status = await self._pool.execute(
            _INSERT_DECISION,
            station_id,
            observation_timestamp,
            dust_id,
            sensor_result,
            anomaly_result,
            object_result,
            json.dumps(result_payload, default=str),
            image_b64,
        )
        # asyncpg: "INSERT 0 1"(삽입) vs "INSERT 0 0"(ON CONFLICT)
        return status.endswith(" 1")

    async def dead_letter(
        self,
        frame_id: int,
        attempts: int,
        last_error: str,
        source_row: dict,
    ) -> None:
        """포이즌 메시지를 decision_db.transfer_dlq 로 격리(멱등 upsert)."""
        await self._pool.execute(
            _UPSERT_DLQ,
            frame_id,
            attempts,
            last_error[:2000],
            json.dumps(source_row, default=str),
        )
