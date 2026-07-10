"""In-process smoke test for main._run_loas.

Skips the real database (mock pool).  The point is to prove the wiring:
* `_run_loas` honours the configured DUST/CCTV ports
* both listeners are accepting TCP connections after boot
* `stop_event` cleanly tears the whole assembly down

End-to-end behaviour against postgres is already covered by the
per-component repo / handler / correlator integration tests; this file
fills the gap for top-level boot orchestration.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock

import pytest
import structlog

from ingestion_gateway.config import IngestionSettings
from ingestion_gateway.main import _run_loas


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _try_connect(port: int) -> bool:
    """Return True if a TCP connection to localhost:port succeeds."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as sock:
            sock.close()
        return True
    except OSError:
        return False


def _try_connect_refused(port: int) -> bool:
    """Return True if a TCP connection is refused (i.e. nothing is listening)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2) as sock:
            sock.close()
        return False
    except OSError:
        return True


@pytest.mark.asyncio
async def test_loas_mode_binds_both_ports_and_stops_cleanly(tmp_path):
    dust_port = _free_port()
    cctv_port = _free_port()

    settings = IngestionSettings(
        _env_file=None,
        protocol="loas",
        tcp_host="127.0.0.1",
        loas_dust_port=dust_port,
        loas_cctv_port=cctv_port,
        loas_amr_id="amr-smoke",
        storage_root=str(tmp_path),
        loas_correlator_interval_sec=0.05,  # quick ticks so we see one
        db_password="x",                    # dummy, pool is mocked
    )

    # Mocked asyncpg pool — execute() returns the standard tag string.
    pool = AsyncMock()
    pool.execute.return_value = "UPDATE 0"
    pool.fetchval.return_value = 1
    pool.close = AsyncMock(return_value=None)

    stop_event = asyncio.Event()
    logger = structlog.get_logger("test_smoke")

    task = asyncio.create_task(_run_loas(settings, pool, stop_event, logger))
    # Let asyncio.start_server complete bind on both ports.
    await asyncio.sleep(0.15)

    assert _try_connect(dust_port), f"DUST listener not bound on {dust_port}"
    assert _try_connect(cctv_port), f"CCTV listener not bound on {cctv_port}"

    # Correlator should have ticked at least once (mock pool counts calls).
    assert pool.execute.await_count >= 1

    # Trigger graceful shutdown.
    stop_event.set()
    await asyncio.wait_for(task, timeout=5.0)

    # After stop, ports should be free again.
    await asyncio.sleep(0.1)
    assert _try_connect_refused(dust_port), "DUST port still listening after stop"
    assert _try_connect_refused(cctv_port), "CCTV port still listening after stop"


@pytest.mark.asyncio
async def test_loas_dust_listener_actually_consumes_a_frame(tmp_path):
    """One end-to-end frame proves the handler/repo wiring inside
    _run_loas, not just the bind."""
    from gw_proto.codec.loas.constants import (
        DOID_DUST_INSPECTION,
        PROTOCOL_VERSION,
        SOP_DUST,
    )
    from gw_proto.codec.loas.dust_framing import DustHeader, pack_header

    dust_port = _free_port()
    cctv_port = _free_port()

    settings = IngestionSettings(
        _env_file=None,
        protocol="loas",
        tcp_host="127.0.0.1",
        loas_dust_port=dust_port,
        loas_cctv_port=cctv_port,
        loas_amr_id="amr-smoke",
        storage_root=str(tmp_path),
        loas_correlator_interval_sec=60.0,  # don't tick during the test
        db_password="x",
    )

    pool = AsyncMock()
    pool.fetchval.return_value = 1   # both dust repo insert + cctv repo insert
    pool.execute.return_value = "UPDATE 0"
    pool.close = AsyncMock(return_value=None)

    stop_event = asyncio.Event()
    logger = structlog.get_logger("test_smoke")
    task = asyncio.create_task(_run_loas(settings, pool, stop_event, logger))
    await asyncio.sleep(0.15)

    # Send a well-formed DUST frame.
    xml = b"<ELEMENT><CMD_ID>DUST_INSPECTION_INFOR</CMD_ID></ELEMENT>"
    hdr = pack_header(DustHeader(
        sop=SOP_DUST,
        data_object_id=DOID_DUST_INSPECTION,
        version=PROTOCOL_VERSION,
        encryption=0,
        timestamp=1700000000,
        length=len(xml),
    ))
    reader, writer = await asyncio.open_connection("127.0.0.1", dust_port)
    writer.write(hdr + xml)
    await writer.drain()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    await asyncio.sleep(0.15)  # let handler land the INSERT

    # The handler should have routed the INSERT through fetchval.
    assert pool.fetchval.await_count == 1

    stop_event.set()
    await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_unknown_protocol_raises(monkeypatch):
    """Top-level run() should reject bogus protocol values up front."""
    from ingestion_gateway.main import run

    monkeypatch.setenv("IGW_PROTOCOL", "definitely-not-real")
    # Block the actual DB pool from being created — we expect the
    # validation to raise before we reach that point... but in case the
    # current implementation creates the pool first, we still want to
    # surface the ValueError.  So patch create_pool to a no-op pool.
    pool = AsyncMock()
    pool.close = AsyncMock(return_value=None)

    async def fake_create_pool(_settings):
        return pool

    monkeypatch.setattr(
        "ingestion_gateway.main.create_pool", fake_create_pool
    )

    with pytest.raises(ValueError, match="Unknown IGW_PROTOCOL"):
        await run()
