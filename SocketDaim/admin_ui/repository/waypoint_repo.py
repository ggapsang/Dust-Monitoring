"""Waypoint-label CRUD + discovered-waypoints listing (gw_admin role).

LOAS 모드에서 "관측 개소" 는 dust_inspection 의 **target_id (waypoint_id != NULL 행)**
마다 하나씩 자동 생성된다.  station_id 는 target_id 의 결정론적 해시
(loas_station_id(target_id) DB 함수) → 같은 target_id 는 항상 같은 UUID.

이 repo 는 사람 친화 라벨(이름·위치 설명)을 그 station_id 에 붙이는 보조 CRUD.
실제 station 합성은 v_loas_stations VIEW 가 수행.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg


class WaypointLabelAdminRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ---- discovered waypoints (read-side) -------------------------------
    async def list_discovered(self) -> list[dict[str, Any]]:
        """dust_inspection 의 distinct target_id(개소) 별 한 행.

        각 행: 합성 station_id + target_id + 라벨(없으면 null) + 통계
        (sample_count, last_seen_at).  waypoint_id/target_id 가 NULL 인 행은 제외.
        """
        rows = await self._pool.fetch(
            """
            WITH groups AS (
              SELECT
                  loas_station_id(target_id)              AS station_id,
                  target_id,
                  COUNT(*)                                AS sample_count,
                  MAX(received_at)                        AS last_seen_at
                FROM dust_inspection
               WHERE waypoint_id IS NOT NULL
                 AND target_id  IS NOT NULL
               GROUP BY target_id
            )
            SELECT
                g.station_id::text                        AS station_id,
                g.target_id,
                g.sample_count,
                g.last_seen_at,
                wl.label,
                wl.location,
                wl.notes,
                wl.updated_at
              FROM groups g
              LEFT JOIN waypoint_label wl ON wl.station_id = g.station_id
             ORDER BY g.target_id
            """
        )
        return [dict(r) for r in rows]

    # ---- CRUD ------------------------------------------------------------
    async def get(self, station_id: uuid.UUID) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            """
            SELECT station_id, target_id,
                   label, location, notes, created_at, updated_at
              FROM waypoint_label
             WHERE station_id = $1
            """,
            station_id,
        )
        return dict(row) if row else None

    async def upsert(
        self,
        *,
        station_id: uuid.UUID,
        target_id: int | None,
        label: str,
        location: str | None,
        notes: str | None,
    ) -> dict[str, Any]:
        """INSERT 또는 UPDATE (station_id 충돌 시 사용자 필드 갱신).

        target_id 는 audit/display 용 — station_id 가 그 해시이므로 station_id 만
        일치하면 매핑은 유지된다.
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO waypoint_label (
                station_id, target_id, label, location, notes
            ) VALUES (
                $1, $2, $3, $4, $5
            )
            ON CONFLICT (station_id) DO UPDATE
                SET label      = EXCLUDED.label,
                    location   = EXCLUDED.location,
                    notes      = EXCLUDED.notes,
                    updated_at = NOW()
            RETURNING station_id, target_id,
                      label, location, notes, created_at, updated_at
            """,
            station_id, target_id, label, location, notes,
        )
        assert row is not None
        return dict(row)

    async def delete(self, station_id: uuid.UUID) -> bool:
        result = await self._pool.execute(
            "DELETE FROM waypoint_label WHERE station_id = $1",
            station_id,
        )
        return result.endswith(" 1")

    # ---- recent raw XML (디버깅용 토글 패널) ---------------------------
    async def recent_dust_xml(self, limit: int = 10) -> list[dict[str, Any]]:
        """최근 dust_inspection N건의 received_at + 7-tuple + raw_xml.

        raw_xml 컬럼은 항상 채워져 있다 (DUST 핸들러 4단계 중 3단계). UI
        의 토글 패널이 비개발자에게 "AMR 이 진짜 어떤 XML 을 보내는지"
        를 직접 보여줄 때 사용.
        """
        rows = await self._pool.fetch(
            """
            SELECT received_at,
                   waypoint_id,
                   waypoint_x, waypoint_y, waypoint_z,
                   inspection_pan, inspection_tilt, inspection_lift,
                   dust_value,
                   ugv_id, mission_id,
                   raw_xml
              FROM dust_inspection
             ORDER BY received_at DESC
             LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]

    # ---- recent activity (실시간 수신 패널용) ---------------------------
    async def recent_activity(self, limit: int = 30) -> list[dict[str, Any]]:
        """dust_inspection + cctv_frame 의 최근 row 를 시간순 병합."""
        rows = await self._pool.fetch(
            """
            (SELECT received_at,
                    'DUST'::text                                  AS kind,
                    waypoint_id::text                             AS ref,
                    ('dust=' || to_char(dust_value, 'FM999990.0000')
                              || ' alarm=' || dust_alarm)::text   AS detail
               FROM dust_inspection
              WHERE received_at > NOW() - INTERVAL '5 minutes'
              ORDER BY received_at DESC
              LIMIT $1)
            UNION ALL
            (SELECT received_at,
                    'CCTV'::text                                  AS kind,
                    amr_id                                        AS ref,
                    (resolution || ' · ' || byte_size || ' bytes')::text AS detail
               FROM cctv_frame
              WHERE received_at > NOW() - INTERVAL '5 minutes'
              ORDER BY received_at DESC
              LIMIT $1)
            ORDER BY received_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]
