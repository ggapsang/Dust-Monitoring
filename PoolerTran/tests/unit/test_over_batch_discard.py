"""A안(waypoint 당 1배치 + 초과분 폐기) 검증 — _process_waypoint_batch.

설계: 한 waypoint 의 큐 프레임이 batch_size(PT_BATCH_SIZE)를 넘으면 batch_size 만큼만
1콜로 처리하고, 같은 (amr, waypoint) 의 남은 초과분은 단일 DELETE 로 폐기한다.
폐기 건수는 stats["discarded_over_batch"] + WARN 로깅으로 가시화한다.
처리 실패(REST 오류 등 진전 0)면 초과분을 폐기하지 않는다(재시도/6h sweep 백스톱).
"""

from __future__ import annotations

from datetime import datetime, timezone

from poolertran.poller import Poller
from poolertran.repository import QueueRow


def _row(frame_id: int) -> QueueRow:
    return QueueRow(
        frame_id=frame_id,
        attempts=0,
        enqueued_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        dust_id=1000 + frame_id,          # 행마다 고유 dust_id
        cf_id=frame_id,
        amr_id="amr-01",
        file_path=f"/data/storage/cctv/{frame_id}.jpg",
        resolution="V1080",
        received_at=datetime(2026, 6, 5, 14, 0, frame_id % 60, tzinfo=timezone.utc),
        paired_at=None,
        dust_value=float(frame_id),       # 대표(max)는 마지막 프레임
        dust_alarm=3,
        waypoint_id=7,
        target_id=70,
        mission_id=1,
        waypoint_x=1.0,
        waypoint_y=2.0,
        waypoint_z=3.0,
    )


# --- fakes ----------------------------------------------------------------
class _AsyncCM:
    """async with 용 no-op 컨텍스트 매니저 (지정 값을 반환)."""

    def __init__(self, value=None):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    def transaction(self):
        return _AsyncCM()


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _AsyncCM(self._conn)


class FakeQueue:
    """waypoint 큐를 메모리로 모사. fetch 는 앞에서 limit 개 반환, delete 는 그 행 제거,
    purge_waypoint_remainder 는 남은 행 전부 삭제하고 개수 반환(= 초과 폐기)."""

    def __init__(self, rows: list[QueueRow]):
        self._store = list(rows)
        self.deleted: list[int] = []
        self.bumped: list[int] = []
        self.purged_total = 0

    async def fetch_batch_for_waypoint(self, conn, amr_id, waypoint_id, limit):
        return self._store[:limit]                       # 가장 먼저 들어온 limit 개

    async def delete(self, conn, frame_id):
        self.deleted.append(frame_id)
        self._store = [r for r in self._store if r.frame_id != frame_id]

    async def bump_attempts(self, conn, frame_id):
        self.bumped.append(frame_id)

    async def purge_waypoint_remainder(self, conn, amr_id, waypoint_id):
        n = len(self._store)                             # 처리분 delete 후 남은 = 초과분
        self.purged_total += n
        self._store = []
        return n


class FakeRest:
    is_batch = True

    def __init__(self, fail=False):
        self.fail = fail
        self.calls: list[tuple] = []

    async def send_batch(self, amr_id, waypoint_id, rows):
        self.calls.append((amr_id, waypoint_id, [r.frame_id for r in rows]))
        if self.fail:
            raise RuntimeError("rest 500")
        first = rows[0].file_path if rows else None
        result = {"score": 0.5, "path1": first, "path2": first}
        return 200, [dict(result), dict(result)]


class FakeDecision:
    def __init__(self):
        self.inserted: list[dict] = []

    async def fetch_thresholds(self):
        return {"dust": 0.0, "static": 0.0, "dynamic": 0.0}

    async def insert_decision(self, **kw):
        self.inserted.append(kw)
        return True

    async def dead_letter(self, *a, **k):
        pass


def _poller(queue, rest, decision, *, batch_size=3) -> Poller:
    return Poller(
        gw_pool=FakePool(FakeConn()),
        queue=queue,
        rest=rest,
        decision=decision,
        gw_dsn="",
        interval_sec=5,
        batch_size=batch_size,
        max_attempts=10,
        use_listen=False,
    )


# --- tests ----------------------------------------------------------------
async def test_overflow_processes_one_batch_and_discards_remainder():
    # batch_size=3, 큐엔 7개 → 3개만 처리(REST 1콜/insert 1행), 나머지 4개 폐기.
    q = FakeQueue([_row(i) for i in range(1, 8)])      # frame_id 1..7
    rest, d = FakeRest(), FakeDecision()
    p = _poller(q, rest, d, batch_size=3)

    await p._process_waypoint_batch("amr-01", 7)

    assert len(rest.calls) == 1                         # waypoint 당 REST 정확히 1콜
    assert rest.calls[0][2] == [1, 2, 3]                # 가장 먼저 들어온 3개만 전송
    assert len(d.inserted) == 1                         # decision_record 1행
    assert q.deleted == [1, 2, 3]                       # 처리분만 정상 삭제
    assert q.purged_total == 4                          # 초과분 4개 폐기
    assert p.stats["discarded_over_batch"] == 4         # 폐기 카운트(로깅 신호)
    assert p.stats["processed_ok"] == 3


async def test_normal_under_batch_no_discard():
    # batch_size=3, 큐엔 2개 → 분할/폐기 없이 기존과 동일.
    q = FakeQueue([_row(1), _row(2)])
    rest, d = FakeRest(), FakeDecision()
    p = _poller(q, rest, d, batch_size=3)

    await p._process_waypoint_batch("amr-01", 7)

    assert len(rest.calls) == 1
    assert q.deleted == [1, 2]
    assert q.purged_total == 0                          # 폐기 호출되더라도 남은 행 0
    assert p.stats["discarded_over_batch"] == 0
    assert p.stats["processed_ok"] == 2


async def test_exactly_batch_no_overflow_no_discard():
    # 정확히 batch_size(3)개 → 처리만, 초과분 0 → discarded 0.
    q = FakeQueue([_row(1), _row(2), _row(3)])
    rest, d = FakeRest(), FakeDecision()
    p = _poller(q, rest, d, batch_size=3)

    await p._process_waypoint_batch("amr-01", 7)

    assert q.deleted == [1, 2, 3]
    assert q.purged_total == 0
    assert p.stats["discarded_over_batch"] == 0


async def test_rest_failure_does_not_discard_remainder():
    # 초과 상태(7개)라도 REST 실패면 진전 0 → 초과분 폐기 안 함(재시도/6h sweep 위임).
    q = FakeQueue([_row(i) for i in range(1, 8)])
    rest, d = FakeRest(fail=True), FakeDecision()
    p = _poller(q, rest, d, batch_size=3)

    await p._process_waypoint_batch("amr-01", 7)

    assert q.deleted == []                              # 처리 실패 → 삭제 없음
    assert q.bumped == [1, 2, 3]                        # 시도한 3개만 attempts++
    assert q.purged_total == 0                          # 폐기하지 않음
    assert p.stats["discarded_over_batch"] == 0
    assert len(q._store) == 7                           # 큐 그대로 유지
