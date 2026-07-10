"""Unit tests for ingestion_gateway.handler.loas_cctv_handler.

Repository is mocked; filesystem ops go to pytest's tmp_path so we can
assert directory layout and orphan-file cleanup.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ingestion_gateway.handler.loas_cctv_handler import LoasCctvHandler


PEER = ("10.0.0.42", 41234)
JPG = b"\xFF\xD8\xFF\xE0pretendjpeg\xFF\xD9"


def _handler(tmp_path: Path) -> tuple[LoasCctvHandler, AsyncMock]:
    repo = AsyncMock()
    repo.insert.return_value = 1
    h = LoasCctvHandler(repo, tmp_path, amr_id="amr-01")
    return h, repo


@pytest.mark.asyncio
async def test_happy_path_writes_file_and_row(tmp_path):
    h, repo = _handler(tmp_path)
    await h.on_frame(JPG, "V1080", PEER)

    # Exactly one file under cctv/amr-01/<date>/<hour>/
    files = list(tmp_path.glob("cctv/amr-01/*/*/*.jpg"))
    assert len(files) == 1
    f = files[0]
    assert f.read_bytes() == JPG
    assert f.name.endswith("_V1080.jpg")

    # Repo got called with arguments that agree with the file.
    assert repo.insert.call_count == 1
    kwargs = repo.insert.call_args.kwargs
    assert kwargs["amr_id"] == "amr-01"
    assert kwargs["source_ip"] == "10.0.0.42"
    assert kwargs["resolution"] == "V1080"
    assert kwargs["byte_size"] == len(JPG)
    assert kwargs["file_path"] == str(f)
    # received_at must be the same instant the filename encodes.
    fname_us = int(f.name.split("_")[0])
    row_us = int(kwargs["received_at"].timestamp() * 1_000_000)
    assert fname_us == row_us


@pytest.mark.asyncio
async def test_filename_includes_resolution_tag(tmp_path):
    h, repo = _handler(tmp_path)
    await h.on_frame(JPG, "V720p", PEER)
    files = list(tmp_path.glob("cctv/amr-01/*/*/*.jpg"))
    assert files and files[0].name.endswith("_V720p.jpg")


@pytest.mark.asyncio
async def test_multiple_frames_share_parent_dir_under_normal_rate(tmp_path):
    h, repo = _handler(tmp_path)
    for _ in range(5):
        await h.on_frame(JPG, "V1080", PEER)
    files = sorted(tmp_path.glob("cctv/amr-01/*/*/*.jpg"))
    assert len(files) == 5
    # All five within the same date+hour directory (test runs in ms)
    parents = {f.parent for f in files}
    assert len(parents) == 1


@pytest.mark.asyncio
async def test_insert_failure_unlinks_file(tmp_path):
    h, repo = _handler(tmp_path)
    repo.insert.side_effect = RuntimeError("DB unavailable")

    await h.on_frame(JPG, "V1080", PEER)

    files = list(tmp_path.glob("cctv/amr-01/*/*/*.jpg"))
    assert files == []  # orphan file was unlinked


@pytest.mark.asyncio
async def test_write_failure_skips_insert(tmp_path, monkeypatch):
    """If the JPG can't hit disk we must NOT call repo.insert()."""
    h, repo = _handler(tmp_path)

    # Force the write step to fail.
    async def _boom(self, path, body):
        raise OSError("disk full")
    monkeypatch.setattr(LoasCctvHandler, "_write_atomically", _boom)

    await h.on_frame(JPG, "V1080", PEER)
    repo.insert.assert_not_called()


@pytest.mark.asyncio
async def test_subdir_override(tmp_path):
    """custom subdir argument lets us point at /cctv-alt or similar."""
    repo = AsyncMock()
    repo.insert.return_value = 1
    h = LoasCctvHandler(repo, tmp_path, amr_id="amr-01", subdir="custom")
    await h.on_frame(JPG, "V640p", PEER)
    files = list(tmp_path.glob("custom/amr-01/*/*/*.jpg"))
    assert len(files) == 1
