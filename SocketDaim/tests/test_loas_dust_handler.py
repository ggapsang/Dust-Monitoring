"""Unit tests for ingestion_gateway.handler.loas_dust_handler.

Repository is mocked — these tests verify only the handler's contract:
* well-formed XML → repo.insert() called exactly once with the right args
* malformed body / decode error → no insert, no exception escapes
* repo exception → no exception escapes (one-way push, can't retry)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from gw_proto.codec.loas.constants import (
    DOID_DUST_INSPECTION,
    PROTOCOL_VERSION,
    SOP_DUST,
)
from gw_proto.codec.loas.dust_framing import DustHeader

from ingestion_gateway.handler.loas_dust_handler import LoasDustHandler


def _header(length: int) -> DustHeader:
    return DustHeader(
        sop=SOP_DUST,
        data_object_id=DOID_DUST_INSPECTION,
        version=PROTOCOL_VERSION,
        encryption=0,
        timestamp=1_700_000_000,
        length=length,
    )


SAMPLE_XML = b"""<ELEMENT>
    <CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>
    <DUST_DATA>0.0400</DUST_DATA>
    <DUST_ALARM>3</DUST_ALARM>
    <DATETIME>2026-05-23 18:30:00.000000</DATETIME>
    <UGV_ID>1</UGV_ID>
    <MISSION_ID>1734498123456</MISSION_ID>
</ELEMENT>"""


@pytest.mark.asyncio
async def test_happy_path_inserts_one_row():
    repo = AsyncMock()
    repo.insert.return_value = 42
    h = LoasDustHandler(repo)

    hdr = _header(len(SAMPLE_XML))
    await h.on_frame(hdr, SAMPLE_XML, ("127.0.0.1", 5000))

    assert repo.insert.call_count == 1
    call_args = repo.insert.call_args
    # Positional: (hdr, payload)
    assert call_args.args[0] is hdr
    payload = call_args.args[1]
    assert payload.cmd_id == "DUST_INSPECTION_INFOR"
    assert payload.dust_data == 0.04
    assert payload.dust_alarm == 3
    assert payload.ugv_id == 1
    assert payload.mission_id == 1734498123456
    # Keyword: raw_xml = decoded text
    assert "DUST_INSPECTION_INFOR" in call_args.kwargs["raw_xml"]


@pytest.mark.asyncio
async def test_malformed_xml_does_not_insert():
    repo = AsyncMock()
    h = LoasDustHandler(repo)
    bad = b"<ELEMENT><CMD_ID>"  # truncated

    await h.on_frame(_header(len(bad)), bad, ("127.0.0.1", 5000))

    repo.insert.assert_not_called()


@pytest.mark.asyncio
async def test_undecodable_bytes_does_not_insert():
    repo = AsyncMock()
    h = LoasDustHandler(repo)
    # 0x80 starter byte: invalid in UTF-8 and not a valid EUC-KR lead byte
    body = b"\x80\x81\x82\x83"

    await h.on_frame(_header(len(body)), body, ("127.0.0.1", 5000))

    repo.insert.assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_cmd_id_does_not_insert():
    repo = AsyncMock()
    h = LoasDustHandler(repo)
    body = (
        b"<ELEMENT>"
        b"<CMD_ID>SOMETHING_ELSE</CMD_ID>"
        b"</ELEMENT>"
    )

    await h.on_frame(_header(len(body)), body, ("127.0.0.1", 5000))

    repo.insert.assert_not_called()


@pytest.mark.asyncio
async def test_repo_failure_swallowed():
    """Repository raising must not escape; one bad write can't stall the
    listener — there's no way to ask the peer to retry."""
    repo = AsyncMock()
    repo.insert.side_effect = RuntimeError("simulated DB failure")
    h = LoasDustHandler(repo)

    await h.on_frame(_header(len(SAMPLE_XML)), SAMPLE_XML, ("127.0.0.1", 5000))
    # Reaching here means no exception escaped.
    assert repo.insert.call_count == 1


# -- XML file dump -----------------------------------------------------------


SAMPLE_XML_WITH_WP = b"""<ELEMENT>
    <CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>
    <DUST_DATA>0.0400</DUST_DATA>
    <DUST_ALARM>3</DUST_ALARM>
    <DATETIME>2026-05-23 18:30:00.000000</DATETIME>
    <WAYPOINT_ID>7</WAYPOINT_ID>
    <UGV_ID>1</UGV_ID>
</ELEMENT>"""


@pytest.mark.asyncio
async def test_dump_disabled_writes_no_file(tmp_path):
    """``dump_dir=None`` is the default — no disk activity at all."""
    repo = AsyncMock()
    repo.insert.return_value = 1
    h = LoasDustHandler(repo)  # dump_dir defaults to None

    await h.on_frame(_header(len(SAMPLE_XML_WITH_WP)), SAMPLE_XML_WITH_WP, ("127.0.0.1", 5000))

    assert list(tmp_path.rglob("*.xml")) == []


@pytest.mark.asyncio
async def test_dump_enabled_writes_xml_to_disk(tmp_path):
    repo = AsyncMock()
    repo.insert.return_value = 1
    h = LoasDustHandler(repo, dump_dir=tmp_path, dump_interval_sec=0.0)

    await h.on_frame(_header(len(SAMPLE_XML_WITH_WP)), SAMPLE_XML_WITH_WP, ("127.0.0.1", 5000))

    files = list(tmp_path.rglob("*.xml"))
    assert len(files) == 1
    # Path: {tmp}/{YYYY-MM-DD}/{HH}/{epoch_us}_wp7.xml
    assert files[0].name.endswith("_wp7.xml")
    assert "DUST_INSPECTION_INFOR" in files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_dump_throttle_skips_within_interval(tmp_path):
    """Two frames received <interval apart → only one file written."""
    repo = AsyncMock()
    repo.insert.return_value = 1
    # 60s interval — second call must be skipped.
    h = LoasDustHandler(repo, dump_dir=tmp_path, dump_interval_sec=60.0)

    await h.on_frame(_header(len(SAMPLE_XML_WITH_WP)), SAMPLE_XML_WITH_WP, ("127.0.0.1", 5000))
    await h.on_frame(_header(len(SAMPLE_XML_WITH_WP)), SAMPLE_XML_WITH_WP, ("127.0.0.1", 5000))

    files = list(tmp_path.rglob("*.xml"))
    assert len(files) == 1


@pytest.mark.asyncio
async def test_dump_failure_does_not_block_db_insert(tmp_path):
    """An OSError mid-dump must not propagate — DB INSERT already succeeded."""
    repo = AsyncMock()
    repo.insert.return_value = 99
    h = LoasDustHandler(repo, dump_dir=tmp_path, dump_interval_sec=0.0)
    # Make the dump directory un-creatable: create a *file* at the date path.
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    blocker = tmp_path / now.strftime("%Y-%m-%d")
    blocker.write_text("not a directory")

    await h.on_frame(_header(len(SAMPLE_XML_WITH_WP)), SAMPLE_XML_WITH_WP, ("127.0.0.1", 5000))

    # DB insert still happened; no exception escaped.
    assert repo.insert.call_count == 1


@pytest.mark.asyncio
async def test_dump_not_attempted_when_db_insert_fails(tmp_path):
    """If the row is not persisted, the disk dump is irrelevant — skip it
    so we don't leave orphan XML files claiming a row that does not exist."""
    repo = AsyncMock()
    repo.insert.side_effect = RuntimeError("simulated DB failure")
    h = LoasDustHandler(repo, dump_dir=tmp_path, dump_interval_sec=0.0)

    await h.on_frame(_header(len(SAMPLE_XML_WITH_WP)), SAMPLE_XML_WITH_WP, ("127.0.0.1", 5000))

    assert list(tmp_path.rglob("*.xml")) == []
