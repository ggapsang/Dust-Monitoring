"""Batch processing failure/edge paths for the Poller (in-memory fakes).

Verifies the core invariants from PoolerTran_설계.md on the batch+decision path:
  - ordering ① REST → ② decision_record INSERT → ③ queue DELETE (success)
  - orphan rows (cf_id IS NULL) are deleted without a REST call (§8.2)
  - transient REST/insert failure bumps attempts and keeps the row (§8.2)
  - poison message (> max_attempts) goes to DLQ and leaves the queue (§8.4)
  - a decision_db failure on the success path is treated as a retryable error
  - a DLQ write failure keeps the row rather than losing it

The happy-path classification is covered in test_decision_batch.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

from poolertran.poller import Poller
from poolertran.repository import QueueRow


def _row(**over) -> QueueRow:
    base = dict(
        frame_id=1,
        attempts=0,
        enqueued_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        dust_id=99,
        cf_id=1,
        amr_id="amr-01",
        file_path="/x.jpg",
        resolution="V1080",
        received_at=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
        paired_at=datetime(2026, 6, 5, 14, 0, 1, tzinfo=timezone.utc),
        dust_value=1.0,
        dust_alarm=3,
        waypoint_id=7,
        target_id=70,
        mission_id=1,
        waypoint_x=1.0,
        waypoint_y=2.0,
        waypoint_z=3.0,
    )
    base.update(over)
    return QueueRow(**base)


class FakeQueue:
    def __init__(self):
        self.deleted: list[int] = []
        self.bumped: list[int] = []

    async def delete(self, conn, frame_id):
        self.deleted.append(frame_id)

    async def bump_attempts(self, conn, frame_id):
        self.bumped.append(frame_id)


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
    def __init__(self, fail_insert=False, fail_dlq=False):
        self.inserted: list[dict] = []
        self.dlq: list[tuple] = []
        self._fail_insert = fail_insert
        self._fail_dlq = fail_dlq

    async def fetch_thresholds(self):
        return {"dust": 0.0, "static": 0.0, "dynamic": 0.0}

    async def insert_decision(self, **kw):
        if self._fail_insert:
            raise RuntimeError("decision_db down")
        self.inserted.append(kw)
        return True

    async def dead_letter(self, frame_id, attempts, last_error, source_row):
        if self._fail_dlq:
            raise RuntimeError("decision_db down")
        self.dlq.append((frame_id, attempts, last_error))


def _poller(queue, rest, decision, *, max_attempts=10) -> Poller:
    return Poller(
        gw_pool=None,
        queue=queue,
        rest=rest,
        decision=decision,
        gw_dsn="",
        interval_sec=5,
        batch_size=100,
        max_attempts=max_attempts,
        use_listen=False,
    )


_CONN = object()  # fakes ignore the connection argument


async def test_success_orders_rest_decision_then_delete():
    q, rest, d = FakeQueue(), FakeRest(), FakeDecision()
    p = _poller(q, rest, d)
    await p._process_batch_rows(_CONN, "amr-01", 7, [_row(frame_id=1), _row(frame_id=2)])

    assert rest.calls and rest.calls[0][2] == [1, 2]
    assert len(d.inserted) == 1          # 배치당 decision_record 1행
    assert q.deleted == [1, 2]           # deleted only after decision write
    assert q.bumped == []
    assert p.stats["processed_ok"] == 2


async def test_orphans_deleted_without_rest():
    q, rest, d = FakeQueue(), FakeRest(), FakeDecision()
    p = _poller(q, rest, d)
    rows = [_row(frame_id=1, cf_id=None), _row(frame_id=2)]  # one orphan + one real
    await p._process_batch_rows(_CONN, "amr-01", 7, rows)

    assert rest.calls[0][2] == [2]       # only the real frame is sent
    assert q.deleted == [1, 2]           # orphan dropped directly, real after decision
    assert p.stats["orphans_purged"] == 1
    assert p.stats["processed_ok"] == 1


async def test_all_orphans_skip_rest_entirely():
    q, rest, d = FakeQueue(), FakeRest(), FakeDecision()
    p = _poller(q, rest, d)
    await p._process_batch_rows(_CONN, "amr-01", 7, [_row(frame_id=1, cf_id=None)])

    assert rest.calls == []              # nothing to send
    assert q.deleted == [1]
    assert p.stats["orphans_purged"] == 1


async def test_rest_failure_bumps_all_rows():
    q, rest, d = FakeQueue(), FakeRest(fail=True), FakeDecision()
    p = _poller(q, rest, d, max_attempts=10)
    await p._process_batch_rows(
        _CONN, "amr-01", 7, [_row(frame_id=1, attempts=2), _row(frame_id=2, attempts=2)]
    )

    assert q.bumped == [1, 2]
    assert q.deleted == []               # rows stay for retry
    assert d.inserted == [] and d.dlq == []
    assert p.stats["failed"] == 2


async def test_decision_db_failure_is_retryable():
    q, rest, d = FakeQueue(), FakeRest(), FakeDecision(fail_insert=True)
    p = _poller(q, rest, d)
    await p._process_batch_rows(_CONN, "amr-01", 7, [_row(frame_id=4, attempts=0)])

    assert rest.calls                    # REST happened
    assert q.deleted == []               # but not deleted — decision write failed
    assert q.bumped == [4]               # bumped for retry
    assert p.stats["failed"] == 1


async def test_poison_message_dead_letters_and_removes():
    q, rest, d = FakeQueue(), FakeRest(fail=True), FakeDecision()
    p = _poller(q, rest, d, max_attempts=10)
    # attempts=10 → attempts+1=11 > max(10) → DLQ
    await p._process_batch_rows(_CONN, "amr-01", 7, [_row(frame_id=7, attempts=10)])

    assert d.dlq and d.dlq[0][0] == 7
    assert q.deleted == [7]              # removed from queue
    assert q.bumped == []
    assert p.stats["dead_lettered"] == 1


async def test_dlq_write_failure_falls_back_to_bump():
    # decision_db down even for DLQ → keep the row rather than lose it.
    q, rest = FakeQueue(), FakeRest(fail=True)
    d = FakeDecision(fail_dlq=True)
    p = _poller(q, rest, d, max_attempts=10)
    await p._process_batch_rows(_CONN, "amr-01", 7, [_row(frame_id=8, attempts=10)])

    assert q.deleted == []              # not dropped despite exceeding cap
    assert q.bumped == [8]
