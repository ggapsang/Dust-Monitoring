"""gateway_db 읽기 — decision_record.dust_id → dust_inspection 24컬럼.

egress 는 decision_db(판정)와 gateway_db(원천 센서) 두 인스턴스를 읽는다(cross-DB).
Postgres 간 직접 조인은 불가하므로, 판정 행의 dust_id 로 dust_inspection 1행을
조회해 Python 에서 LOAS 행으로 합친다.  gw_reader(SELECT) 권한 사용.
"""

from __future__ import annotations

import asyncpg

# LOAS t_inspection 에 필요한 25컬럼(이미지/event_id 제외)의 원천.
# target_index 는 t_inspection 의 FK(plant_id 와 묶인 복합 FK 가능)에 필요하므로
# 반드시 함께 읽어야 한다 — 누락 시 INSERT 가 기본값(0)으로 채워 FK 위반.
_SELECT_DUST = """
SELECT sensor_datetime, sensor_type, sensor_index, target_index, dust_value,
       waypoint_x, waypoint_y, waypoint_z,
       location_id, map_id, navigation_id, exec_id, plant_id, target_id,
       ugv_id, waypoint_id, inspection_local_id,
       inspection_pan, inspection_tilt, inspection_lift, object_id,
       rot_x, rot_y, rot_z, rot_w
  FROM dust_inspection
 WHERE id = $1
"""


class GatewayRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch_dust(self, dust_id: int) -> dict | None:
        """dust_inspection 1행을 dict 로.  없으면 None(원천 purge/누락)."""
        row = await self._pool.fetchrow(_SELECT_DUST, dust_id)
        return dict(row) if row is not None else None
