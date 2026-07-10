from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from poolertran.repository import QueueRow
from poolertran.rest_client import (
    BatchPathsRestClient,
    DemoRestClient,
    create_rest_client,
)


def _row(**over) -> QueueRow:
    base = dict(
        frame_id=42,
        attempts=0,
        enqueued_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        dust_id=99,
        cf_id=42,
        amr_id="amr-01",
        file_path="/data/storage/cctv/amr-01/2026-06-05/14/x.jpg",
        resolution="V1080",
        received_at=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
        paired_at=datetime(2026, 6, 5, 14, 0, 1, tzinfo=timezone.utc),
        dust_value=1.23,
        dust_alarm=2,
        waypoint_id=7,
        target_id=70,
        mission_id=1001,
        waypoint_x=1.0,
        waypoint_y=2.0,
        waypoint_z=3.0,
    )
    base.update(over)
    return QueueRow(**base)


def test_is_orphan():
    assert _row(cf_id=None).is_orphan is True
    assert _row(cf_id=42).is_orphan is False


# --- 데모(더미) REST 모드 (batch_paths 단독) -------------------------------
@dataclass
class _Settings:
    rest_mode: str = "batch_paths"
    rest_url: str = "http://x/ingest"
    rest_timeout_sec: float = 10.0
    rest_demo: bool = False
    rest_demo_score: float = 0.5
    rest_demo_image_path: str = ""


def test_factory_returns_real_client_when_demo_off():
    c = create_rest_client(_Settings(rest_mode="batch_paths", rest_demo=False))
    assert isinstance(c, BatchPathsRestClient)


def test_factory_returns_demo_batch():
    d = create_rest_client(_Settings(rest_mode="batch_paths", rest_demo=True))
    assert isinstance(d, DemoRestClient) and d.is_batch is True
    assert BatchPathsRestClient.is_batch is True


async def test_demo_send_batch_returns_dual_first_path():
    # 고정 image_path 없음 → 실제 API 와 동일한 dual 결과 list[2],
    # path1·path2 = 첫 입력 프레임 경로(rows[0]).
    d = DemoRestClient(score=0.5)
    rows = [_row(frame_id=i, file_path=f"/img/{i}.jpg") for i in range(3)]
    status, body = await d.send_batch("amr-01", 5, rows)
    assert status == 200
    assert isinstance(body, list) and len(body) == 2   # [정적, 동적]
    for elem in body:
        assert elem["score"] == 0.5
        assert elem["path1"] == "/img/0.jpg"           # 첫 입력 경로
        assert elem["path2"] == "/img/0.jpg"
        assert set(elem) == {"score", "path1", "path2"}


async def test_demo_send_batch_fixed_image_path_overrides():
    # 고정 image_path 지정 시 그것을 path1·path2 로 우선 echo.
    d = DemoRestClient(score=0.3, image_path="/fixed/out.jpg")
    rows = [_row(frame_id=i, file_path=f"/img/{i}.jpg") for i in range(3)]
    _, body = await d.send_batch("amr-01", 5, rows)
    assert [e["path1"] for e in body] == ["/fixed/out.jpg", "/fixed/out.jpg"]
    assert body[0]["score"] == 0.3


def test_factory_validates_mode_even_in_demo():
    import pytest
    with pytest.raises(ValueError):
        create_rest_client(_Settings(rest_mode="bogus", rest_demo=True))
