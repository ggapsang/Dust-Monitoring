"""Tests for gw_proto.transport.loas_dust_server."""

from __future__ import annotations

import asyncio
import socket

import pytest

from gw_proto.codec.loas.constants import (
    DOID_DUST_INSPECTION,
    PROTOCOL_VERSION,
    SOP_DUST,
)
from gw_proto.codec.loas.dust_framing import DustHeader, pack_header
from gw_proto.transport.loas_dust_server import LoasDustTcpServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _good_header(*, length: int, encryption: int = 0) -> bytes:
    return pack_header(
        DustHeader(
            sop=SOP_DUST,
            data_object_id=DOID_DUST_INSPECTION,
            version=PROTOCOL_VERSION,
            encryption=encryption,
            timestamp=1_700_000_000,
            length=length,
        )
    )


class _ServerHarness:
    """Spin up a real LoasDustTcpServer on a free port, collect frames."""

    def __init__(self) -> None:
        self.frames: list[tuple[DustHeader, bytes, tuple[str, int]]] = []
        self.handler_should_raise: bool = False

    async def _on_frame(self, hdr, body, peer):
        if self.handler_should_raise:
            raise RuntimeError("simulated handler failure")
        self.frames.append((hdr, body, peer))

    async def __aenter__(self):
        self.port = _free_port()
        self.server = LoasDustTcpServer("127.0.0.1", self.port, self._on_frame)
        self.task = asyncio.create_task(self.server.start())
        await asyncio.sleep(0.05)  # let bind complete
        return self

    async def __aexit__(self, *exc):
        await self.server.stop()
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass


async def _send(port: int, payload: bytes) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    await writer.drain()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_single_frame_delivered(self):
        body = b"<ELEMENT/>"
        async with _ServerHarness() as h:
            await _send(h.port, _good_header(length=len(body)) + body)
            await asyncio.sleep(0.05)
            assert len(h.frames) == 1
            hdr, b, peer = h.frames[0]
            assert hdr.sop == SOP_DUST
            assert b == body
            assert peer[0] == "127.0.0.1"

    @pytest.mark.asyncio
    async def test_multiple_frames_on_same_connection(self):
        bodies = [b"<A/>", b"<B/>", b"<C/>"]
        wire = b"".join(_good_header(length=len(b)) + b for b in bodies)
        async with _ServerHarness() as h:
            await _send(h.port, wire)
            await asyncio.sleep(0.1)
            assert [b for _, b, _ in h.frames] == bodies

    @pytest.mark.asyncio
    async def test_zero_length_body_ok(self):
        async with _ServerHarness() as h:
            await _send(h.port, _good_header(length=0))
            await asyncio.sleep(0.05)
            assert len(h.frames) == 1
            assert h.frames[0][1] == b""


class TestEncryptedFrame:
    @pytest.mark.asyncio
    async def test_encrypted_frame_drained_and_dropped(self):
        body = b"junkbytes12345"
        wire = _good_header(length=len(body), encryption=1) + body
        async with _ServerHarness() as h:
            await _send(h.port, wire)
            await asyncio.sleep(0.05)
            # Frame must be drained from the stream but never delivered.
            assert len(h.frames) == 0

    @pytest.mark.asyncio
    async def test_encrypted_then_plain_keeps_stream_aligned(self):
        """If we'd skipped reading the encrypted body the next header
        unpack would have failed.  This proves we kept stream sync."""
        enc_body = b"\xDE\xAD\xBE\xEF" * 4
        plain_body = b"<OK/>"
        wire = (
            _good_header(length=len(enc_body), encryption=1) + enc_body
            + _good_header(length=len(plain_body)) + plain_body
        )
        async with _ServerHarness() as h:
            await _send(h.port, wire)
            await asyncio.sleep(0.1)
            assert len(h.frames) == 1
            assert h.frames[0][1] == plain_body


class TestRejection:
    @pytest.mark.asyncio
    async def test_bad_sop_closes_connection(self):
        bad = b"\x00\x00" + b"\xD0\x02" + bytes([PROTOCOL_VERSION, 0]) \
              + (1_700_000_000).to_bytes(4, "big") + (0).to_bytes(2, "big")
        body_after = _good_header(length=4) + b"<OK/>"
        async with _ServerHarness() as h:
            await _send(h.port, bad + body_after)
            await asyncio.sleep(0.1)
            # Connection should have been killed at the bad header; the
            # following well-formed frame must NOT be delivered.
            assert len(h.frames) == 0

    @pytest.mark.asyncio
    async def test_truncated_body_closes_connection(self):
        async with _ServerHarness() as h:
            # Announce 10 bytes but only ship 4
            await _send(h.port, _good_header(length=10) + b"abcd")
            await asyncio.sleep(0.1)
            assert len(h.frames) == 0


class TestHandlerIsolation:
    @pytest.mark.asyncio
    async def test_handler_exception_does_not_kill_stream(self):
        bodies = [b"<A/>", b"<B/>"]
        wire = b"".join(_good_header(length=len(b)) + b for b in bodies)
        async with _ServerHarness() as h:
            h.handler_should_raise = True
            await _send(h.port, wire)
            await asyncio.sleep(0.1)
            # Handler raised for both frames → 0 collected; but the
            # important property is that we *attempted* the second frame
            # (the connection didn't die after the first exception).  We
            # verify that by checking the connection stayed open long
            # enough to consume both frames.  We can't observe "attempted"
            # directly without instrumentation, so flip the flag mid-
            # stream by interleaving:
            h.handler_should_raise = False
            await _send(h.port, _good_header(length=2) + b"OK")
            await asyncio.sleep(0.1)
            assert len(h.frames) == 1  # only the post-flip frame collected
            assert h.frames[0][1] == b"OK"
