"""배치 모드 decision_record 생산 경로 + 분류 헬퍼 테스트 (in-memory fakes)."""

from __future__ import annotations

from datetime import datetime, timezone

from poolertran.poller import (
    Poller,
    _classify,
    _extract_dual,
    _representative_row,
)
from poolertran.repository import QueueRow


def _row(**over) -> QueueRow:
    base = dict(
        frame_id=1, attempts=0,
        enqueued_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        dust_id=99, cf_id=1, amr_id="amr-01", file_path="/x.jpg",
        resolution="V1080",
        received_at=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
        paired_at=None, dust_value=0.1, dust_alarm=3,
        waypoint_id=7, target_id=70, mission_id=1,
        waypoint_x=1.0, waypoint_y=2.0, waypoint_z=3.0,
    )
    base.update(over)
    return QueueRow(**base)


# -- helpers ---------------------------------------------------------------
def test_extract_dual_list_of_tuples():
    body = [[0.9, "s1", "s2"], [0.1, "d1", "d2"]]
    assert _extract_dual(body) == (0.9, 0.1)


def test_extract_dual_single_broadcasts():
    # 단일 dict(데모) → 정적=동적 동일값
    assert _extract_dual({"score": 0.7, "image_path": "/r.jpg"}) == (0.7, 0.7)


def test_classify_boundaries():
    assert _classify(0.8, 0.5) == "abnormal"
    assert _classify(0.5, 0.5) == "normal"     # > 가 아니라 boundary 는 normal
    assert _classify(0.2, 0.5) == "normal"
    assert _classify(None, 0.5) == "normal"    # 값 없음 → 보수적 normal
    assert _classify(0.9, None) == "normal"    # 임계 없음 → normal


def test_representative_is_max_dust():
    rows = [_row(frame_id=1, dust_id=11, dust_value=0.3),
            _row(frame_id=2, dust_id=22, dust_value=0.8),
            _row(frame_id=3, dust_id=33, dust_value=None)]
    rep = _representative_row(rows)
    assert rep.dust_id == 22 and rep.dust_value == 0.8


# -- batch decision flow ---------------------------------------------------
class FakeQueue:
    def __init__(self): self.deleted = []
    async def delete(self, conn, frame_id): self.deleted.append(frame_id)
    async def bump_attempts(self, conn, frame_id): pass


class FakeDecision:
    def __init__(self): self.inserted = []
    async def fetch_thresholds(self): return {"dust": 0.5, "static": 0.5, "dynamic": 0.5}
    async def insert_decision(self, **kw): self.inserted.append(kw); return True


class FakeBatchRest:
    is_batch = True
    def __init__(self, body): self._body = body
    async def send_batch(self, amr_id, waypoint_id, rows): return 200, self._body


def _poller(queue, decision, rest):
    return Poller(
        gw_pool=None, queue=queue, rest=rest, decision=decision, gw_dsn="",
        interval_sec=5, batch_size=100, max_attempts=10, use_listen=False,
    )


async def test_batch_writes_decision_record_with_classified_channels():
    q, d = FakeQueue(), FakeDecision()
    # 정적 0.9(>0.5 → abnormal), 동적 0.1(<0.5 → normal)
    rest = FakeBatchRest([[0.9, "s1", "s2"], [0.1, "d1", "d2"]])
    p = _poller(q, d, rest)
    rows = [_row(frame_id=1, dust_id=11, dust_value=0.3),
            _row(frame_id=2, dust_id=22, dust_value=0.8)]  # 대표 = dust 0.8 → sensor abnormal
    await p._process_batch_rows(_CONN, "amr-01", 7, rows)

    assert len(d.inserted) == 1
    rec = d.inserted[0]
    assert rec["sensor_result"] == "abnormal"     # dust 0.8 > 0.5
    assert rec["anomaly_result"] == "abnormal"    # static 0.9 > 0.5
    assert rec["object_result"] == "normal"       # dynamic 0.1 < 0.5
    assert rec["dust_id"] == 22                    # 대표(최댓값) 측정의 dust_id
    assert rec["result_payload"] == [[0.9, "s1", "s2"], [0.1, "d1", "d2"]]
    assert q.deleted == [1, 2]                     # 전송 성공 → 큐 전체 삭제
    assert p.stats["processed_ok"] == 2


_CONN = object()
