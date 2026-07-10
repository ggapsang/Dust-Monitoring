"""Polling loop — claim queue rows, POST a waypoint batch to REST, write the
decision_record, DELETE.

Processing order per batch is strictly: ① REST → ② decision_record INSERT
(decision_db) → ③ queue DELETE (PoolerTran_설계.md §8.2).  ③ happens only after
② succeeds, so a crash between ② and ③ re-delivers the frames and the idempotent
INSERT (dust_id UNIQUE → ON CONFLICT DO NOTHING) absorbs it (at-least-once).

A whole batch is claimed inside a single gateway transaction holding
``FOR UPDATE OF q SKIP LOCKED`` locks, so concurrent PoolerTran instances never
process the same row.  The REST call runs while those locks are held, so keep
``PT_BATCH_SIZE`` modest — the queue is a backlog (small) by design (§4.1).
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import structlog

from .repository import (
    DecisionProducer,
    QueueRepository,
    QueueRow,
    WaypointFrame,
)
from .rest_client import RestClient

logger = structlog.get_logger(__name__)

_NOTIFY_CHANNEL = "cctv_transfer"


def _extract_score_image(body) -> tuple[float | None, str | None]:  # noqa: ANN001
    """REST 응답 body 에서 (score, image_path) 추출.  필드 누락/형식 불일치는 None."""
    if not isinstance(body, dict):
        return None, None
    score = body.get("score")
    image_path = body.get("image_path") or body.get("file_path")
    return score, image_path


def _one_result(elem) -> float | None:  # noqa: ANN001
    """배치 응답 1원소 → score(float|None).  원소는 [score, p1, p2] 또는
    {score, ...} 또는 단일 score 무엇이든 허용."""
    if isinstance(elem, (list, tuple)) and elem:
        try:
            return float(elem[0])
        except (TypeError, ValueError):
            return None
    if isinstance(elem, dict):
        v = elem.get("score")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    try:
        return float(elem)
    except (TypeError, ValueError):
        return None


def _extract_dual(body) -> tuple[float | None, float | None]:  # noqa: ANN001
    """배치 REST 응답 → (정적 score, 동적 score).

    형식: [(score,p1,p2)정적, (score,p1,p2)동적].  JSON 상 list[2].
    단일(dict {score}) 응답(데모 등)은 정적=동적 동일값으로 broadcast."""
    if isinstance(body, (list, tuple)) and len(body) >= 2:
        return _one_result(body[0]), _one_result(body[1])
    if isinstance(body, (list, tuple)) and len(body) == 1:
        s = _one_result(body[0])
        return s, s
    if isinstance(body, dict):  # 단일 결과(데모) → 양쪽에 broadcast
        s, _ = _extract_score_image(body)
        return s, s
    return None, None


def _classify(value: float | None, threshold: float | None) -> str:
    """value > threshold 면 'abnormal', 아니면 'normal'.  값/임계 없으면 보수적 'normal'."""
    if value is None or threshold is None:
        return "normal"
    return "abnormal" if value > threshold else "normal"


def _representative_row(rows: list[QueueRow]) -> QueueRow:
    """배치의 대표 측정 = dust_value 최댓값 행(센서 채널·dust_id 연결의 기준).
    dust_value 없는 행은 -inf 취급."""
    return max(rows, key=lambda r: (r.dust_value if r.dust_value is not None else float("-inf")))


def _static_p1(body) -> str | None:  # noqa: ANN001
    """배치 응답에서 정적분석 결과의 첫 이미지 경로(path1).
    형식 [[score,p1,p2](정적), ...].  dict 형식([{path1|image_path}, ..])도 허용."""
    if isinstance(body, (list, tuple)) and body:
        first = body[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            return first[1]
        if isinstance(first, dict):
            return first.get("path1") or first.get("image_path") or first.get("file_path")
    if isinstance(body, dict):  # 단일(데모)
        return body.get("path1") or body.get("image_path") or body.get("file_path")
    return None


async def _encode_b64(path: str | None) -> str | None:
    """경로의 파일을 읽어 Base64 문자열로.  경로 없음/읽기 실패는 None(전송엔 영향 없음)."""
    if not path:
        return None
    try:
        data = await asyncio.to_thread(Path(path).read_bytes)
        return base64.b64encode(data).decode("ascii")
    except OSError:
        logger.warning("image_read_failed", path=path)
        return None


class Poller:
    def __init__(
        self,
        gw_pool: asyncpg.Pool,
        queue: QueueRepository,
        rest: RestClient,
        decision: DecisionProducer,
        *,
        gw_dsn: str,
        interval_sec: float,
        batch_size: int,
        max_attempts: int,
        use_listen: bool,
        init_waypoint_id: int = -1,
        queue_max_age_sec: float = 21600,
    ) -> None:
        self._gw_pool = gw_pool
        self._queue = queue
        self._rest = rest
        # 배치 결과 생산자: waypoint 배치 REST 결과 → decision_record(decision_db).
        self._decision = decision
        self._gw_dsn = gw_dsn
        self._interval = interval_sec
        self._batch = batch_size
        self._max_attempts = max_attempts
        self._use_listen = use_listen

        self._notify_event = asyncio.Event()
        self._listen_conn: asyncpg.Connection | None = None

        # waypoint 전환 감지 상태 (docs/waypoint_transition_batch.md).
        # AMR 별로 직전 waypoint 를 추적(다중 AMR 안전).  새로 보는 amr_id 의 기본값은
        # sentinel(init_waypoint_id, 예: -1) → 첫 실제 waypoint 가 항상 "신규"로 인식.
        # in-memory; 재시작 시 초기화.
        self._init_waypoint_id: int = init_waypoint_id
        self._last_waypoint_by_amr: dict[str, int] = {}
        self._last_frame_id: int | None = None
        self._last_dust_id: int | None = None

        # 오래된 큐 행 정리 임계값(초).  0 이하면 비활성.
        self._queue_max_age_sec = queue_max_age_sec

        # Lightweight counters for /health (설계 §13 monitoring).
        self.stats = {
            "processed_ok": 0,
            "failed": 0,
            "dead_lettered": 0,
            "orphans_purged": 0,
            "waypoint_transitions": 0,
            "discarded_over_batch": 0,
        }

    # -- lifecycle ---------------------------------------------------------

    async def start_listener(self) -> None:
        """LISTEN on a dedicated connection so notifications wake the loop
        immediately (저지연 모드).  The polling loop stays as the backup so a
        missed notification (consumer down) is still picked up (설계 §8.5)."""
        if not self._use_listen:
            return
        self._listen_conn = await asyncpg.connect(self._gw_dsn)
        await self._listen_conn.add_listener(_NOTIFY_CHANNEL, self._on_notify)
        logger.info("listener_started", channel=_NOTIFY_CHANNEL)

    async def stop_listener(self) -> None:
        if self._listen_conn is not None:
            try:
                await self._listen_conn.remove_listener(_NOTIFY_CHANNEL, self._on_notify)
            except Exception:  # noqa: BLE001
                pass
            await self._listen_conn.close()
            self._listen_conn = None

    def _on_notify(self, conn, pid, channel, payload) -> None:  # noqa: ANN001, ARG002
        self._notify_event.set()

    # -- main loop ---------------------------------------------------------

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info(
            "poller_started",
            interval_sec=self._interval,
            batch_size=self._batch,
            use_listen=self._use_listen,
        )
        await self.start_listener()
        try:
            while not stop_event.is_set():
                # waypoint-batch 모델: 평상시엔 큐에 쌓기만 하고, waypoint 가 바뀌는
                # 순간에만 직전 waypoint 배치를 처리(REST→result→큐 DELETE)한다.
                try:
                    await self._check_waypoint_transition()
                except Exception:
                    logger.exception("waypoint_transition_error")
                try:
                    await self._sweep_stale()
                except Exception:
                    logger.exception("queue_sweep_error")
                await self._wait(stop_event)
        finally:
            await self.stop_listener()
            logger.info("poller_stopped")

    async def _wait(self, stop_event: asyncio.Event) -> None:
        """Sleep until the poll interval elapses, a NOTIFY arrives, or stop."""
        if self._use_listen:
            waiter = asyncio.create_task(self._notify_event.wait())
            stopper = asyncio.create_task(stop_event.wait())
            _, pending = await asyncio.wait(
                {waiter, stopper},
                timeout=self._interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            self._notify_event.clear()
        else:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def _sweep_stale(self) -> int:
        """오래된(정상 처리 시점을 한참 넘긴) 큐 행 정리 — 안전망.

        임계값(_queue_max_age_sec)이 0 이하면 비활성.  임계값은 단일 waypoint
        체류시간보다 충분히 커야 정상 처리 전 프레임을 지우지 않는다."""
        if self._queue_max_age_sec <= 0:
            return 0
        deleted = await self._queue.sweep_stale(self._queue_max_age_sec)
        if deleted:
            logger.warning(
                "queue_stale_swept", deleted=deleted, max_age_sec=self._queue_max_age_sec
            )
        return deleted

    # -- waypoint 전환 감지 (docs/waypoint_transition_batch.md) -------------

    async def _check_waypoint_transition(self) -> list[WaypointFrame]:
        """AMR 별로 현재 waypoint 가 직전과 달라졌으면, 그 amr 의 직전 waypoint
        배치를 처리(REST→result→큐 DELETE)한다.  처리한 프레임 목록
        (frame_id, dust_id, amr_id, received_at, file_path) 합본을 반환.

        다중 AMR 안전: amr_id 별로 독립 비교.  처음 보는 amr 의 직전값은 sentinel."""
        current = await self._queue.fetch_current_waypoints()  # {amr_id: waypoint_id}
        processed: list[WaypointFrame] = []

        for amr_id, current_wp in current.items():
            prev_wp = self._last_waypoint_by_amr.get(amr_id, self._init_waypoint_id)
            if current_wp != prev_wp:
                self.stats["waypoint_transitions"] += 1
                logger.info(
                    "waypoint_transition",
                    amr_id=amr_id,
                    prev_waypoint_id=prev_wp,
                    current_waypoint_id=current_wp,
                )
                # 직전 waypoint 의 (해당 amr) 큐 행을 처리 + 삭제.
                processed.extend(await self._process_waypoint_batch(amr_id, prev_wp))
            self._last_waypoint_by_amr[amr_id] = current_wp

        return processed

    async def _process_waypoint_batch(
        self, amr_id: str, waypoint_id: int
    ) -> list[WaypointFrame]:
        """(amr_id, waypoint) 의 큐 행을 처리한다 — **waypoint 당 배치 1건만**.

        ① batch_size(PT_BATCH_SIZE) 만큼만 claim(FOR UPDATE SKIP LOCKED) → ②REST 1콜 +
        decision_record 1행 + 그 프레임 큐 DELETE.  정상 흐름은 프레임이 batch_size 미만이라
        여기서 끝난다.  비정상으로 한 waypoint 에 batch_size 를 넘게 쌓이면(예: 정체/폭주),
        **처리분(batch_size)만 남기고 남은 초과분은 폐기**(잘못된/불필요 데이터로 간주)하고
        `discarded_over_batch` 로 WARN 로깅한다.  처리 실패(REST 오류 등)면 진전이 없어
        초과분을 폐기하지 않고 재시도/6h sweep(_sweep_stale) 백스톱에 맡긴다.
        큐 DELETE 는 오직 이 경로(waypoint 전환 시점)에서만 일어난다.
        처리한 프레임의 (frame_id, dust_id, amr_id, received_at, file_path) 목록을 반환."""
        handled: list[WaypointFrame] = []
        async with self._gw_pool.acquire() as conn:
            async with conn.transaction():
                cleared_before = (
                    self.stats["processed_ok"]
                    + self.stats["dead_lettered"]
                    + self.stats["orphans_purged"]
                )
                rows = await self._queue.fetch_batch_for_waypoint(
                    conn, amr_id, waypoint_id, self._batch
                )
                # waypoint 분량을 1콜로 전송 후 일괄 적재(decision_record)·삭제.
                await self._process_batch_rows(conn, amr_id, waypoint_id, rows)
                for row in rows:
                    handled.append(
                        WaypointFrame(
                            frame_id=row.frame_id,
                            dust_id=row.dust_id,
                            amr_id=row.amr_id,
                            received_at=row.received_at,
                            file_path=row.file_path,
                        )
                    )
                claimed = len(rows)
                cleared_after = (
                    self.stats["processed_ok"]
                    + self.stats["dead_lettered"]
                    + self.stats["orphans_purged"]
                )
                # A안: batch_size 만큼 꽉 찼고(=초과분 존재 가능) 이번 배치가 실제로 처리(진전)
                # 됐으면, 같은 (amr, waypoint) 의 남은 초과분을 전부 폐기한다.  처리 실패면
                # (진전 0) 폐기하지 않고 재시도/6h sweep 에 맡긴다.
                if claimed == self._batch and cleared_after > cleared_before:
                    discarded = await self._queue.purge_waypoint_remainder(
                        conn, amr_id, waypoint_id
                    )
                    if discarded:
                        self.stats["discarded_over_batch"] += discarded
                        logger.warning(
                            "discarded_over_batch",
                            amr_id=amr_id,
                            waypoint_id=waypoint_id,
                            kept=self._batch,
                            discarded=discarded,
                        )
        logger.info(
            "waypoint_batch_processed",
            amr_id=amr_id,
            waypoint_id=waypoint_id,
            processed=len(handled),
            frames=[
                {
                    "frame_id": f.frame_id,
                    "dust_id": f.dust_id,
                    "amr_id": f.amr_id,
                    "received_at": f.received_at.isoformat() if f.received_at else None,
                    "file_path": f.file_path,
                }
                for f in handled
            ],
        )
        return handled

    # -- batch processing --------------------------------------------------

    async def _process_batch_rows(
        self,
        conn: asyncpg.Connection,
        amr_id: str,
        waypoint_id: int,
        rows: list[QueueRow],
    ) -> None:
        """(amr_id, waypoint_id) + frames 를 1콜로 전송하고, 성공 시
        ② decision_record(decision_db) 1행 INSERT → ③ 프레임 전체 큐 DELETE.

        REST 결과 = 정적/동적 2쌍(score+이미지경로).  dust_value(대표=최댓값)·두 score 를
        임계로 2단계 분류 → 3채널.  final_decision 은 pending(=decision_agent 가 판정).
        전송/적재 실패 시 각 행을 _handle_failure(attempts++/DLQ)로 보낸다."""
        if not rows:
            return
        # Orphan 행(source cctv_frame 이 cleaner 로 purge 됨 → 이미지 없음)은 REST 로
        # 보내지 않고 큐에서 직접 제거(설계 §8.2).
        orphans = [r for r in rows if r.is_orphan]
        rows = [r for r in rows if not r.is_orphan]
        for row in orphans:
            await self._queue.delete(conn, row.frame_id)
            self.stats["orphans_purged"] += 1
            logger.info("source_purged", frame_id=row.frame_id)
        if not rows:
            return
        try:
            status, body = await self._rest.send_batch(amr_id, waypoint_id, rows)  # ① REST(1콜)
        except Exception as exc:  # noqa: BLE001
            for row in rows:
                await self._handle_failure(conn, row, exc)
            return

        received = [r.received_at for r in rows if r.received_at]
        try:
            # ② decision_record: dust_value(대표)·정적/동적 score 분류 → 3채널 1행.
            th = await self._decision.fetch_thresholds()
            static_score, dynamic_score = _extract_dual(body)
            rep = _representative_row(rows)
            sensor_res = _classify(rep.dust_value, th.get("dust"))
            anomaly_res = _classify(static_score, th.get("static"))
            object_res = _classify(dynamic_score, th.get("dynamic"))
            obs_ts = rep.received_at or (max(received) if received else None) \
                or datetime.now(timezone.utc)
            # LOAS image_data 용: 정적 p1 이미지를 읽어 Base64 로 저장(egress 가 직접 INSERT).
            image_b64 = await _encode_b64(_static_p1(body))
            await self._decision.insert_decision(
                station_id=f"{amr_id}:wp{waypoint_id}",
                observation_timestamp=obs_ts,
                dust_id=rep.dust_id,
                sensor_result=sensor_res,
                anomaly_result=anomaly_res,
                object_result=object_res,
                result_payload=body,
                image_b64=image_b64,
            )
            logged = {
                "sensor": sensor_res, "anomaly": anomaly_res, "object": object_res,
                "static_score": static_score, "dynamic_score": dynamic_score,
                "rep_dust_id": rep.dust_id, "rep_dust_value": rep.dust_value,
            }
        except Exception as exc:  # noqa: BLE001 - 적재 실패 → 큐 유지/재시도
            for row in rows:
                await self._handle_failure(conn, row, exc)
            return

        for row in rows:                                             # ③ 프레임 전체 큐 DELETE
            await self._queue.delete(conn, row.frame_id)
            self.stats["processed_ok"] += 1
            self._last_frame_id = row.frame_id
            self._last_dust_id = row.dust_id
        logger.info(
            "waypoint_batch_processed_db",
            amr_id=amr_id,
            waypoint_id=waypoint_id,
            count=len(rows),
            rest_status=status,
            **logged,
        )

    async def _handle_failure(
        self, conn: asyncpg.Connection, row: QueueRow, exc: Exception
    ) -> None:
        attempts = row.attempts + 1
        # Poison message: exceeded the retry cap → move to DLQ and drop from
        # the queue so it stops blocking (설계 §8.4).
        if attempts > self._max_attempts:
            # DLQ 대상: decision_db.transfer_dlq (DecisionProducer.dead_letter).
            dlq = self._decision
            try:
                await dlq.dead_letter(
                    row.frame_id, attempts, repr(exc), source_row=asdict(row)
                )
                await self._queue.delete(conn, row.frame_id)
                self.stats["dead_lettered"] += 1
                logger.error(
                    "dead_letter",
                    frame_id=row.frame_id,
                    attempts=attempts,
                    reason=repr(exc),
                )
                return
            except Exception:
                # DLQ write failed (e.g. decision_db down) — keep the row rather
                # than lose it; fall through to bump for a later retry.
                logger.exception("dead_letter_failed", frame_id=row.frame_id)

        await self._queue.bump_attempts(conn, row.frame_id)
        self.stats["failed"] += 1
        logger.warning(
            "transfer_failed",
            frame_id=row.frame_id,
            attempts=attempts,
            reason=repr(exc),
        )
