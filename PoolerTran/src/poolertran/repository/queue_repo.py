"""cctv_transfer_queue repository (gateway_db).

The queue holds only the *pending backlog* (row exists = unprocessed,
deleted = done — outbox philosophy, PoolerTran_설계.md §4.1).  All queue
operations run as the `cctv_forwarder` role: SELECT/DELETE on the queue,
UPDATE only the `attempts` column, and read-only SELECT on the source
tables (cctv_frame, dust_inspection).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import asyncpg


@dataclass(slots=True)
class QueueRow:
    """One claimed queue row joined to its source frame + dust metadata.

    `cf_id` is NULL when the source cctv_frame has already been removed by
    cleaner retention (orphan queue row) — PoolerTran deletes those directly
    (PoolerTran_설계.md §8.2).
    """

    frame_id: int
    attempts: int
    enqueued_at: datetime
    dust_id: int | None  # cctv_transfer_queue.dust_id (페어링 시점 dust_inspection.id)
    # cctv_frame (LEFT JOIN — may be NULL if source purged)
    cf_id: int | None
    amr_id: str | None
    file_path: str | None
    resolution: str | None
    received_at: datetime | None
    paired_at: datetime | None
    # dust_inspection (LEFT JOIN)
    dust_value: float | None
    dust_alarm: int | None
    waypoint_id: int | None
    target_id: int | None          # 관측 개소 기준값(REST payload 로 전송)
    mission_id: int | None
    waypoint_x: float | None
    waypoint_y: float | None
    waypoint_z: float | None

    @property
    def is_orphan(self) -> bool:
        return self.cf_id is None


@dataclass(slots=True)
class WaypointFrame:
    """직전 waypoint 배치의 한 행 (cctv_frame ⋈ dust_inspection 조인 결과).

    docs/waypoint_transition_batch.md 참조.  frame_id/dust_id 는 트리거 동치성에
    따라 cctv_frame.id / cctv_frame.dust_inspection_id 에서 취한다(큐 잔존과 무관).
    """

    frame_id: int
    dust_id: int | None
    amr_id: str | None
    received_at: datetime | None
    file_path: str | None


# Concurrent-consumer-safe claim.  FOR UPDATE OF q SKIP LOCKED lets us scale
# to multiple PoolerTran instances without double-processing the same row.
# LEFT JOIN (not INNER) so orphan rows — whose source cctv_frame was purged by
# cleaner retention — are still returned and can be cleaned up (설계 §8.1).
_SELECT_BATCH = """
SELECT q.frame_id, q.attempts, q.enqueued_at, q.dust_id,
       cf.id          AS cf_id,
       cf.amr_id      AS amr_id,
       cf.file_path   AS file_path,
       cf.resolution  AS resolution,
       cf.received_at AS received_at,
       cf.paired_at   AS paired_at,
       di.dust_value  AS dust_value,
       di.dust_alarm  AS dust_alarm,
       di.waypoint_id AS waypoint_id,
       di.target_id   AS target_id,
       di.mission_id  AS mission_id,
       di.waypoint_x  AS waypoint_x,
       di.waypoint_y  AS waypoint_y,
       di.waypoint_z  AS waypoint_z
  FROM cctv_transfer_queue q
  LEFT JOIN cctv_frame      cf ON cf.id = q.frame_id
  LEFT JOIN dust_inspection di ON di.id = q.dust_id   -- 안정적 enqueue 시점 dust_id 사용(cf.dust_inspection_id 는 ON DELETE SET NULL 가변)
 ORDER BY q.enqueued_at, q.frame_id
   FOR UPDATE OF q SKIP LOCKED
 LIMIT $1
"""

# 배치(amr_id + waypoint 단위) 처리용 claim — 특정 amr 의 특정 waypoint 행만 claim.
# 처리는 waypoint 전환 시점에만 일어나며, 다중 AMR 안전을 위해 amr_id 도 키로 사용한다
# (docs/waypoint_transition_batch.md).  di.waypoint_id 필터라 orphan(di NULL)은 제외.
_SELECT_BATCH_FOR_WAYPOINT = """
SELECT q.frame_id, q.attempts, q.enqueued_at, q.dust_id,
       cf.id          AS cf_id,
       cf.amr_id      AS amr_id,
       cf.file_path   AS file_path,
       cf.resolution  AS resolution,
       cf.received_at AS received_at,
       cf.paired_at   AS paired_at,
       di.dust_value  AS dust_value,
       di.dust_alarm  AS dust_alarm,
       di.waypoint_id AS waypoint_id,
       di.target_id   AS target_id,
       di.mission_id  AS mission_id,
       di.waypoint_x  AS waypoint_x,
       di.waypoint_y  AS waypoint_y,
       di.waypoint_z  AS waypoint_z
  FROM cctv_transfer_queue q
  LEFT JOIN cctv_frame      cf ON cf.id = q.frame_id
  LEFT JOIN dust_inspection di ON di.id = q.dust_id   -- 안정적 enqueue 시점 dust_id 사용(cf.dust_inspection_id 는 ON DELETE SET NULL 가변)
 WHERE di.waypoint_id = $1 AND cf.amr_id = $2
 ORDER BY q.enqueued_at, q.frame_id
   FOR UPDATE OF q SKIP LOCKED
 LIMIT $3
"""

_DELETE = "DELETE FROM cctv_transfer_queue WHERE frame_id = $1"

_BUMP_ATTEMPTS = (
    "UPDATE cctv_transfer_queue SET attempts = attempts + 1 WHERE frame_id = $1"
)

_DEPTH = "SELECT COUNT(*) AS n FROM cctv_transfer_queue"

# 기동 시 큐 전체 삭제 (재시작 시 이전 작업 폐기).
_CLEAR_ALL = "DELETE FROM cctv_transfer_queue"

# 오래된 행 정리(age sweep): enqueued_at 이 임계 초($1) 보다 오래된 행 삭제.
_SWEEP_STALE = (
    "DELETE FROM cctv_transfer_queue "
    "WHERE enqueued_at < now() - ($1 * interval '1 second')"
)

# A안 초과 폐기: 특정 (amr, waypoint) 의 남은 큐 행 전부 삭제.
# 정상 처리분(배치 = PT_BATCH_SIZE)은 호출 전 이미 frame_id 단위로 DELETE 되었으므로,
# 이 쿼리는 그 waypoint 에 batch_size 를 넘게 쌓인 '초과분'만 지운다(필터는 _SELECT_BATCH_FOR_WAYPOINT 와 동일).
_PURGE_WAYPOINT_REMAINDER = """
DELETE FROM cctv_transfer_queue q
USING cctv_frame cf, dust_inspection di
WHERE cf.id = q.frame_id
  AND di.id = q.dust_id
  AND di.waypoint_id = $1
  AND cf.amr_id = $2
"""

# AMR 별 현재 waypoint_id = 각 amr_id 의 "가장 최근 enqueue 된 큐 행"의 waypoint.
# 큐(백로그)만 보므로 가볍고, 새 waypoint 프레임이 들어오면 즉시 반영된다.
# 다중 AMR 안전: amr_id 별 DISTINCT ON 으로 각각의 현재 위치를 독립 산출.
_CURRENT_WAYPOINTS = """
SELECT DISTINCT ON (cf.amr_id)
       cf.amr_id      AS amr_id,
       di.waypoint_id AS waypoint_id
  FROM cctv_transfer_queue q
  JOIN cctv_frame      cf ON cf.id = q.frame_id
  JOIN dust_inspection di ON di.id = q.dust_id   -- 안정적 enqueue 시점 dust_id 사용
 WHERE di.waypoint_id IS NOT NULL AND cf.amr_id IS NOT NULL
 ORDER BY cf.amr_id, q.enqueued_at DESC, q.frame_id DESC
"""


def _affected(command_status: str) -> int:
    """asyncpg execute() 의 명령 태그(예: 'DELETE 5')에서 영향 행 수 추출."""
    try:
        return int(command_status.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0


def _to_queue_row(r) -> QueueRow:  # noqa: ANN001 - asyncpg.Record
    return QueueRow(
        frame_id=r["frame_id"],
        attempts=r["attempts"],
        enqueued_at=r["enqueued_at"],
        dust_id=r["dust_id"],
        cf_id=r["cf_id"],
        amr_id=r["amr_id"],
        file_path=r["file_path"],
        resolution=r["resolution"],
        received_at=r["received_at"],
        paired_at=r["paired_at"],
        dust_value=r["dust_value"],
        dust_alarm=r["dust_alarm"],
        waypoint_id=r["waypoint_id"],
        target_id=r["target_id"],
        mission_id=r["mission_id"],
        waypoint_x=r["waypoint_x"],
        waypoint_y=r["waypoint_y"],
        waypoint_z=r["waypoint_z"],
    )


class QueueRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def fetch_batch(
        self, conn: asyncpg.Connection, limit: int
    ) -> list[QueueRow]:
        """Claim up to `limit` rows (waypoint 무관, 전체 백로그) on the given
        (in-transaction) connection.

        MUST be called inside ``async with conn.transaction():`` — the
        FOR UPDATE SKIP LOCKED locks are held until that transaction commits.
        (waypoint-batch 모델에서는 미사용; 전량 드레인이 필요할 때를 위해 유지.)
        """
        rows = await conn.fetch(_SELECT_BATCH, limit)
        return [_to_queue_row(r) for r in rows]

    async def fetch_batch_for_waypoint(
        self, conn: asyncpg.Connection, amr_id: str, waypoint_id: int, limit: int
    ) -> list[QueueRow]:
        """특정 amr_id + waypoint 의 큐 행을 claim (waypoint 전환 시 배치 처리용).

        FOR UPDATE SKIP LOCKED 로 잠그며 트랜잭션 안에서 호출해야 한다.
        amr_id 를 키에 포함해 다중 AMR 안전.  (docs/waypoint_transition_batch.md)"""
        rows = await conn.fetch(_SELECT_BATCH_FOR_WAYPOINT, waypoint_id, amr_id, limit)
        return [_to_queue_row(r) for r in rows]

    async def delete(self, conn: asyncpg.Connection, frame_id: int) -> None:
        """Remove a queue row (done / orphan / dead-lettered).  Runs on the
        claiming transaction's connection so the DELETE commits atomically
        with the rest of the batch."""
        await conn.execute(_DELETE, frame_id)

    async def bump_attempts(self, conn: asyncpg.Connection, frame_id: int) -> None:
        """Increment attempts on transient failure — the row stays in the
        queue and is retried on the next poll."""
        await conn.execute(_BUMP_ATTEMPTS, frame_id)

    async def depth(self) -> int:
        """Current backlog size (health/monitoring, 설계 §13)."""
        row = await self._pool.fetchrow(_DEPTH)
        return int(row["n"]) if row else 0

    async def clear(self) -> int:
        """큐 전체 삭제(기동 시).  삭제된 행 수 반환."""
        result = await self._pool.execute(_CLEAR_ALL)
        return _affected(result)

    async def sweep_stale(self, max_age_sec: float) -> int:
        """enqueued_at 이 max_age_sec 보다 오래된 행 삭제.  삭제된 행 수 반환."""
        result = await self._pool.execute(_SWEEP_STALE, max_age_sec)
        return _affected(result)

    async def purge_waypoint_remainder(
        self, conn: asyncpg.Connection, amr_id: str, waypoint_id: int
    ) -> int:
        """(amr_id, waypoint) 의 큐 잔여행 전부 삭제 — A안 초과(batch_size) 폐기.

        처리한 배치(batch_size)는 이미 frame_id 단위로 delete 된 뒤이므로 '남은(초과)'
        행만 지워진다.  배치 처리와 같은 트랜잭션 conn 에서 실행해 원자적으로 커밋한다.
        삭제된 행 수(폐기 건수)를 반환한다."""
        result = await conn.execute(_PURGE_WAYPOINT_REMAINDER, waypoint_id, amr_id)
        return _affected(result)

    async def fetch_current_waypoints(self) -> dict[str, int]:
        """AMR 별 현재 waypoint_id 맵 {amr_id: waypoint_id}.

        각 amr_id 의 가장 최근 enqueue 된 큐 행 기준(다중 AMR 안전).
        (docs/waypoint_transition_batch.md §4.1)"""
        rows = await self._pool.fetch(_CURRENT_WAYPOINTS)
        return {
            r["amr_id"]: int(r["waypoint_id"])
            for r in rows
            if r["amr_id"] is not None and r["waypoint_id"] is not None
        }
