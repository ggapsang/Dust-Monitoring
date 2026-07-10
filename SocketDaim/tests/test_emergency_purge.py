"""Unit tests for cleaner.cleanup.emergency_purge().

Covers every branch of the new pressure-based purge.  Uses a mock
asyncpg.Pool and monkeypatches the disk-usage helper so the test never
has to actually fill a disk.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from cleaner import cleanup
from cleaner.config import CleanerSettings


def _settings(storage_root: str) -> CleanerSettings:
    # All other env defaults are fine.  We only care about the storage
    # path and the purge thresholds in these tests.
    return CleanerSettings(
        db_password="x",
        storage_root=storage_root,
        emergency_purge_at_percent=85,
        emergency_target_percent=70,
        batch_size=2,  # small batch makes multi-iter scenarios obvious
    )


class _PercentSequence:
    """Callable that returns the next value from a queue each time it's
    invoked.  Used to monkeypatch ``cleanup._disk_percent`` so we can
    simulate disk usage falling as files are deleted."""

    def __init__(self, values):
        self._values = list(values)
        self.calls = 0

    def __call__(self, _path):
        self.calls += 1
        if not self._values:
            # Last value sticks once consumed.
            return self._last
        v = self._values.pop(0)
        self._last = v
        return v


def _make_row(frame_id: str, file_path: str) -> dict[str, Any]:
    # asyncpg rows act like mappings; a dict is enough for the production
    # code which only does row["id"] / row["file_path"].  Emergency purge
    # now targets cctv_frame (LOAS JPEGs), whose PK column is ``id``.
    return {"id": frame_id, "file_path": file_path}


def _make_pool(rows_per_fetch):
    """Build an AsyncMock pool whose .fetch() returns the next list from
    rows_per_fetch on each call.  .execute() is also AsyncMock."""
    pool = AsyncMock()
    queue = list(rows_per_fetch)

    async def fetch(_sql, _limit):
        if not queue:
            return []
        return queue.pop(0)

    pool.fetch = AsyncMock(side_effect=fetch)
    pool.execute = AsyncMock(return_value="DELETE 0")
    return pool


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_when_below_threshold(tmp_path, monkeypatch):
    """Disk at 50% → emergency_purge is a no-op, pool is never touched."""
    monkeypatch.setattr(cleanup, "_disk_percent", _PercentSequence([50.0]))
    pool = _make_pool([])

    result = await cleanup.emergency_purge(pool, _settings(str(tmp_path)))

    assert result["activated"] is False
    assert result["current_pct"] == 50.0
    assert result["threshold"] == 85
    pool.fetch.assert_not_called()
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_stat_fails(tmp_path, monkeypatch):
    """If disk_usage raises (returned as None), activation is refused."""
    monkeypatch.setattr(cleanup, "_disk_percent", lambda _p: None)
    pool = _make_pool([])

    result = await cleanup.emergency_purge(pool, _settings(str(tmp_path)))

    assert result == {"activated": False, "reason": "stat_failed"}
    pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_single_pass_until_target(tmp_path, monkeypatch):
    """88% → after one batch the next read says 65% → loop exits.

    _disk_percent calls per pass: entry-guard + iter-1 start (still over)
    + iter-2 start (≤target → break) + final-pct.  Hence 4 values in the
    sequence (last sticks for final).
    """
    f1 = tmp_path / "v1.bin"; f1.write_bytes(b"x")
    f2 = tmp_path / "v2.bin"; f2.write_bytes(b"x")

    seq = _PercentSequence([88.0, 88.0, 65.0])
    monkeypatch.setattr(cleanup, "_disk_percent", seq)

    pool = _make_pool([[
        _make_row("vid-1", str(f1)),
        _make_row("vid-2", str(f2)),
    ]])

    result = await cleanup.emergency_purge(pool, _settings(str(tmp_path)))

    assert result["activated"] is True
    assert result["deleted"] == 2
    assert result["unlink_failures"] == 0
    # 1 batch that did work + 1 break-only check = 2 iterations
    assert result["iterations"] == 2
    assert result["final_pct"] == 65.0
    # Files actually gone.
    assert not f1.exists()
    assert not f2.exists()
    # DELETE batched once with both ids.
    pool.execute.assert_called_once()
    args = pool.execute.call_args
    assert args[0][1] == ["vid-1", "vid-2"]


@pytest.mark.asyncio
async def test_multi_pass_until_target(tmp_path, monkeypatch):
    """Three batches needed to fall from 88 → 80 → 75 → 65."""
    files = []
    rows_per_fetch = []
    for batch in range(3):
        batch_rows = []
        for i in range(2):
            p = tmp_path / f"b{batch}-v{i}.bin"
            p.write_bytes(b"x")
            files.append(p)
            batch_rows.append(_make_row(f"id-{batch}-{i}", str(p)))
        rows_per_fetch.append(batch_rows)

    # entry:88, iter1-start:88, iter2-start:80, iter3-start:75,
    # iter4-start:65 (break). Three batches actually delete.
    monkeypatch.setattr(
        cleanup, "_disk_percent",
        _PercentSequence([88.0, 88.0, 80.0, 75.0, 65.0]),
    )

    pool = _make_pool(rows_per_fetch)

    result = await cleanup.emergency_purge(pool, _settings(str(tmp_path)))

    assert result["activated"] is True
    assert result["deleted"] == 6
    # 3 batches + 1 break-only check
    assert result["iterations"] == 4
    assert all(not p.exists() for p in files)
    assert pool.execute.call_count == 3


@pytest.mark.asyncio
async def test_no_frames_left(tmp_path, monkeypatch):
    """Disk still over threshold but the cctv_frame table is empty.
    Should log and break cleanly, not loop forever."""
    monkeypatch.setattr(
        cleanup, "_disk_percent",
        _PercentSequence([90.0, 90.0, 90.0]),
    )

    pool = _make_pool([[]])  # one empty fetch

    result = await cleanup.emergency_purge(pool, _settings(str(tmp_path)))

    assert result["activated"] is True
    assert result["deleted"] == 0
    assert result["iterations"] == 1
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_all_unlinks_fail_no_progress(tmp_path, monkeypatch):
    """Every unlink raises OSError → successful_ids stays empty → break.
    The DB rows must NOT be deleted (orphan avoidance in emergency mode)."""
    monkeypatch.setattr(
        cleanup, "_disk_percent",
        _PercentSequence([88.0, 88.0, 88.0]),
    )

    pool = _make_pool([[
        _make_row("vid-a", "/nonexistent/a.bin"),
        _make_row("vid-b", "/nonexistent/b.bin"),
    ]])

    def boom(path):
        raise PermissionError(13, "denied", path)
    monkeypatch.setattr(os, "unlink", boom)

    result = await cleanup.emergency_purge(pool, _settings(str(tmp_path)))

    assert result["activated"] is True
    assert result["deleted"] == 0
    assert result["unlink_failures"] == 2
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_partial_unlink_failure(tmp_path, monkeypatch):
    """Half the batch unlinks, half raises OSError.
    DELETE should fire with only the successful id."""
    f1 = tmp_path / "ok.bin"; f1.write_bytes(b"x")
    bad = tmp_path / "bad.bin"; bad.write_bytes(b"x")

    monkeypatch.setattr(
        cleanup, "_disk_percent",
        _PercentSequence([88.0, 88.0, 70.0]),
    )

    real_unlink = os.unlink

    def selective(path):
        if str(path).endswith("bad.bin"):
            raise PermissionError(13, "denied", str(path))
        real_unlink(path)
    monkeypatch.setattr(os, "unlink", selective)

    pool = _make_pool([[
        _make_row("good-id", str(f1)),
        _make_row("bad-id", str(bad)),
    ]])

    result = await cleanup.emergency_purge(pool, _settings(str(tmp_path)))

    assert result["activated"] is True
    assert result["deleted"] == 1
    assert result["unlink_failures"] == 1
    pool.execute.assert_called_once()
    sent_ids = pool.execute.call_args[0][1]
    assert sent_ids == ["good-id"]
    assert not f1.exists()
    assert bad.exists()  # left for the next pass


@pytest.mark.asyncio
async def test_file_not_found_still_deletes_row(tmp_path, monkeypatch):
    """FileNotFoundError means the file is already gone — the DB row
    should be removed so we don't keep selecting it next pass."""
    monkeypatch.setattr(
        cleanup, "_disk_percent",
        _PercentSequence([88.0, 88.0, 70.0]),
    )

    pool = _make_pool([[_make_row("ghost-id", "/already/gone.bin")]])

    def gone(_p):
        raise FileNotFoundError(2, "missing", "/already/gone.bin")
    monkeypatch.setattr(os, "unlink", gone)

    result = await cleanup.emergency_purge(pool, _settings(str(tmp_path)))

    assert result["activated"] is True
    # deleted counter only counts actual unlinks (not FileNotFound)
    assert result["deleted"] == 0
    assert result["unlink_failures"] == 0
    # ...but the DB row is still removed
    pool.execute.assert_called_once()
    assert pool.execute.call_args[0][1] == ["ghost-id"]


@pytest.mark.asyncio
async def test_threshold_is_inclusive(tmp_path, monkeypatch):
    """Usage exactly at threshold (85.0) should trigger purge."""
    monkeypatch.setattr(
        cleanup, "_disk_percent",
        _PercentSequence([85.0, 85.0, 70.0]),
    )

    f = tmp_path / "v.bin"; f.write_bytes(b"x")
    pool = _make_pool([[_make_row("v-id", str(f))]])

    result = await cleanup.emergency_purge(pool, _settings(str(tmp_path)))

    assert result["activated"] is True
    assert result["deleted"] == 1


@pytest.mark.asyncio
async def test_disk_percent_helper_returns_float(tmp_path):
    """The real _disk_percent on the temp dir should give a real number."""
    pct = cleanup._disk_percent(str(tmp_path))
    assert pct is not None
    assert 0 <= pct <= 100


@pytest.mark.asyncio
async def test_disk_percent_helper_handles_bad_path():
    """A nonexistent path returns None instead of raising."""
    pct = cleanup._disk_percent("/nonexistent/path/that/does/not/exist")
    assert pct is None


@pytest.mark.asyncio
async def test_purge_all_frames_deletes_everything(tmp_path):
    """Manual '지금 정리' wipes every frame — files + rows — regardless of
    disk usage or age, looping until the table is empty."""
    f1 = tmp_path / "a.jpg"; f1.write_bytes(b"x")
    f2 = tmp_path / "b.jpg"; f2.write_bytes(b"x")
    f3 = tmp_path / "c.jpg"; f3.write_bytes(b"x")
    # fetch returns batch1, batch2, then [] → loop stops.
    pool = _make_pool([
        [_make_row("1", str(f1)), _make_row("2", str(f2))],
        [_make_row("3", str(f3))],
    ])

    result = await cleanup.purge_all_frames(pool, _settings(str(tmp_path)))

    assert result["deleted"] == 3
    assert not f1.exists() and not f2.exists() and not f3.exists()
    assert pool.execute.call_count == 2
