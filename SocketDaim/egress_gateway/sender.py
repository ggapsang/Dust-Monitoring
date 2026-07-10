"""Decision → LOAS t_inspection 적재.

판정 1건당: decision_record(final_decision/image_b64) + dust_inspection(25컬럼)을
합쳐 t_inspection 1행을 만들고 sink 로 INSERT 한다.  순서: ① outbox 영속화 →
② sink.write(INSERT) → ③ 성공 시 outbox 삭제 + sent_at 갱신(at-least-once).
"""

from __future__ import annotations

import json
import uuid as _uuid
from datetime import datetime
from typing import Any

import structlog

from .outbox import Outbox
from .repository import DecisionRecord, DecisionRepository, GatewayRepository
from .sink import Sink

logger = structlog.get_logger(__name__)

# final_decision → LOAS event_id (검사 결과 코드).
_EVENT_ID = {"normal": 0, "caution": 1, "warning": 2, "danger": 3}


def _build_row(rec: DecisionRecord, dust: dict[str, Any]) -> dict[str, Any]:
    """LOAS t_inspection 1행(키=대상 컬럼명).  dust 25컬럼 + event_id + image_data."""
    dv = dust.get("dust_value")
    return {
        "inspection_datetime": dust.get("sensor_datetime"),
        "event_id": _EVENT_ID.get(rec.final_decision),
        "sensor_type": dust.get("sensor_type"),
        "sensor_index": dust.get("sensor_index"),
        "target_index": dust.get("target_index"),
        "inspection_value": None if dv is None else str(dv),
        "waypoint_x": dust.get("waypoint_x"),
        "waypoint_y": dust.get("waypoint_y"),
        "waypoint_z": dust.get("waypoint_z"),
        "location_id": dust.get("location_id"),
        "map_id": dust.get("map_id"),
        "navigation_id": dust.get("navigation_id"),
        "exec_id": dust.get("exec_id"),
        "plant_id": dust.get("plant_id"),
        "target_id": dust.get("target_id"),
        "ugv_id": dust.get("ugv_id"),
        "waypoint_id": dust.get("waypoint_id"),
        "inspection_loacl_id": dust.get("inspection_local_id"),  # 타깃 철자(loacl)
        "inspection_pan": dust.get("inspection_pan"),
        "inspection_tilt": dust.get("inspection_tilt"),
        "inspection_lift": dust.get("inspection_lift"),
        "object_id": dust.get("object_id"),
        "rot_x": dust.get("rot_x"),
        "rot_y": dust.get("rot_y"),
        "rot_z": dust.get("rot_z"),
        "rot_w": dust.get("rot_w"),
        "image_data": rec.image_b64,
    }


class Sender:
    """판정 1건 → dust 조인 → t_inspection INSERT → sent_at 갱신."""

    def __init__(
        self,
        sink: Sink,
        outbox: Outbox,
        decision_repo: DecisionRepository,
        gateway_repo: GatewayRepository,
        allowed_event_ids: set[int] | None = None,
    ) -> None:
        self._sink = sink
        self._outbox = outbox
        self._repo = decision_repo
        self._gw = gateway_repo
        # 조건부 INSERT: 이 event_id 만 LOAS 에 적재.  None 이면 전부 적재.
        self._allowed_event_ids = allowed_event_ids if allowed_event_ids else {0, 1, 2, 3}

    async def send_record(self, rec: DecisionRecord) -> bool:
        # dust_id 로 gateway_db.dust_inspection 24컬럼 조회(cross-DB).
        dust = await self._gw.fetch_dust(rec.dust_id) if rec.dust_id is not None else None
        if dust is None:
            # 원천 측정 누락(purge 등) → LOAS 행을 만들 수 없음.  무한 재시도 방지 위해
            # sent 처리하고 스킵(경고).
            logger.warning("dust_not_found", decision_id=str(rec.id), dust_id=rec.dust_id)
            await self._repo.mark_sent(rec.id)
            return True

        values = _build_row(rec, dust)
        # 조건부 INSERT: 설정된 event_id 가 아니면 LOAS 적재 생략.
        # 재시도 방지 위해 sent 처리(outbox 에도 넣지 않음).
        event_id = values.get("event_id")
        if event_id not in self._allowed_event_ids:
            logger.info(
                "loas_skipped_event_id",
                decision_id=str(rec.id),
                event_id=event_id,
                allowed=sorted(self._allowed_event_ids),
            )
            await self._repo.mark_sent(rec.id)
            return True
        payload = json.dumps(values, ensure_ascii=False, default=str).encode("utf-8")
        await self._outbox.add(str(rec.id), 0, payload)  # ① outbox 먼저(멱등)
        return await self._write(str(rec.id), values)

    async def replay(self, decision_id: str, msg_type: int, payload: bytes) -> bool:
        """재기동 drain — outbox payload(완성된 t_inspection 행)로 재INSERT."""
        values = json.loads(payload)
        return await self._write(decision_id, values)

    async def _write(self, decision_id: str, values: dict[str, Any]) -> bool:
        try:
            ok = await self._sink.write(values)  # ② INSERT
        except Exception as exc:  # noqa: BLE001
            await self._outbox.bump_attempts(decision_id)
            logger.warning("loas_insert_failed", decision_id=decision_id, reason=str(exc))
            return False
        if not ok:
            await self._outbox.bump_attempts(decision_id)
            return False
        # ③ 성공 → outbox 삭제 + sent_at 갱신
        await self._outbox.remove(decision_id)
        await self._repo.mark_sent(_uuid.UUID(decision_id))
        logger.info("loas_inserted", decision_id=decision_id)
        return True
