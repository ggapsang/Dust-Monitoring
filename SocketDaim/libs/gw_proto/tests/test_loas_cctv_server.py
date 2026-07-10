"""Tests for gw_proto.transport.loas_cctv_server."""

from __future__ import annotations

import asyncio
import socket

import pytest

from gw_proto.codec.loas.cctv_framing import CctvHeader, pack_header
from gw_proto.codec.loas.constants import (
    RESOLUTION_V640P,
    RESOLUTION_V720P,
    RESOLUTION_V1080,
)
from gw_proto.transport.loas_cctv_server import LoasCctvTcpServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _frame(resolution: str, body: bytes) -> bytes:
    return pack_header(CctvHeader(resolution=resolution, length=len(body))) + body


class _ServerHarness:
    def __init__(self, **server_kwargs) -> None:
        self.frames: list[tuple[bytes, str, tuple[str, int]]] = []
        self.handler_should_raise: bool = False
        self._server_kwargs = server_kwargs

    async def _on_frame(self, body, resolution, peer):
        if self.handler_should_raise:
            raise RuntimeError("simulated handler failure")
        self.frames.append((body, resolution, peer))

    async def __aenter__(self):
        self.port = _free_port()
        self.server = LoasCctvTcpServer(
            "127.0.0.1", self.port, self._on_frame, **self._server_kwargs
        )
        self.task = asyncio.create_task(self.server.start())
        await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc):
        await self.server.stop()
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass


async def _send_and_close(port: int, payload: bytes) -> None:
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
        jpg = b"\xFF\xD8\xFF\xE0fakejpeg\xFF\xD9"
        async with _ServerHarness() as h:
            await _send_and_close(h.port, _frame(RESOLUTION_V1080, jpg))
            await asyncio.sleep(0.05)
            assert len(h.frames) == 1
            body, res, peer = h.frames[0]
            assert body == jpg
            assert res == RESOLUTION_V1080
            assert peer[0] == "127.0.0.1"

    @pytest.mark.asyncio
    async def test_multiple_frames_same_connection(self):
        bodies = [b"\xFF\xD8A", b"\xFF\xD8B", b"\xFF\xD8C"]
        wire = b"".join(_frame(RESOLUTION_V720P, b) for b in bodies)
        async with _ServerHarness() as h:
            await _send_and_close(h.port, wire)
            await asyncio.sleep(0.1)
            assert [b for b, _, _ in h.frames] == bodies
            assert {r for _, r, _ in h.frames} == {RESOLUTION_V720P}

    @pytest.mark.asyncio
    async def test_mixed_resolutions(self):
        wire = (
            _frame(RESOLUTION_V1080, b"big")
            + _frame(RESOLUTION_V720P, b"mid")
            + _frame(RESOLUTION_V640P, b"small")
        )
        async with _ServerHarness() as h:
            await _send_and_close(h.port, wire)
            await asyncio.sleep(0.1)
            assert [r for _, r, _ in h.frames] == [
                RESOLUTION_V1080, RESOLUTION_V720P, RESOLUTION_V640P,
            ]


class TestConcurrentConnections:
    @pytest.mark.asyncio
    async def test_concurrent_connections_are_independent(self):
        # Slot-less policy: two simultaneous connections are BOTH served and
        # neither is closed by the server when the other arrives.  (The AMR
        # opens a fresh connection per frame; preempting/closing the previous
        # one would RST a frame still in flight — the field bug we removed.)
        async with _ServerHarness() as h:
            r1, w1 = await asyncio.open_connection("127.0.0.1", h.port)
            r2, w2 = await asyncio.open_connection("127.0.0.1", h.port)
            await asyncio.sleep(0.05)

            # Neither connection is closed by the server just for coexisting:
            # both deliver their frames.
            w1.write(_frame(RESOLUTION_V1080, b"from-conn-1"))
            w2.write(_frame(RESOLUTION_V720P, b"from-conn-2"))
            await w1.drain()
            await w2.drain()
            await asyncio.sleep(0.1)

            assert {b for b, _, _ in h.frames} == {b"from-conn-1", b"from-conn-2"}

            for w in (w1, w2):
                w.close()
                try:
                    await w.wait_closed()
                except Exception:
                    pass

    @pytest.mark.asyncio
    async def test_sequential_reconnect_succeeds(self):
        async with _ServerHarness() as h:
            # First AMR connects, sends, drops.
            await _send_and_close(h.port, _frame(RESOLUTION_V1080, b"first"))
            await asyncio.sleep(0.1)  # let server finish cleanup

            # Reconnect (e.g. AMR's TCP retry) — should succeed.
            await _send_and_close(h.port, _frame(RESOLUTION_V1080, b"second"))
            await asyncio.sleep(0.05)

            assert [b for b, _, _ in h.frames] == [b"first", b"second"]


class TestRejection:
    @pytest.mark.asyncio
    async def test_unknown_resolution_closes_connection(self):
        bad = b"VXXXX" + (3).to_bytes(4, "big") + b"abc"
        followup = _frame(RESOLUTION_V1080, b"would-be-next")
        async with _ServerHarness() as h:
            await _send_and_close(h.port, bad + followup)
            await asyncio.sleep(0.1)
            assert len(h.frames) == 0

    @pytest.mark.asyncio
    async def test_truncated_body_closes_connection(self):
        # Announce 100 bytes, ship 10
        header = pack_header(CctvHeader(resolution=RESOLUTION_V1080, length=100))
        async with _ServerHarness() as h:
            await _send_and_close(h.port, header + b"only-ten!!")
            await asyncio.sleep(0.1)
            assert len(h.frames) == 0


class TestSlotSafety:
    """Regression: a malformed/stalled frame must not pin the single slot."""

    @pytest.mark.asyncio
    async def test_oversized_length_closes_and_frees_slot(self):
        async with _ServerHarness(max_body_bytes=1024) as h:
            oversized = pack_header(
                CctvHeader(resolution=RESOLUTION_V1080, length=10_000)
            )
            await _send_and_close(h.port, oversized + b"xxxxxxxxxx")
            await asyncio.sleep(0.05)
            assert len(h.frames) == 0  # dropped, not read

            # The slot was released → a normal frame is served next.
            await _send_and_close(h.port, _frame(RESOLUTION_V720P, b"after"))
            await asyncio.sleep(0.05)
            assert [b for b, _, _ in h.frames] == [b"after"]

    @pytest.mark.asyncio
    async def test_stalled_body_does_not_pin_slot(self):
        # Announce a body, never send it.  The body read times out and frees
        # the slot rather than blocking every future connection forever.
        async with _ServerHarness(body_read_timeout=0.2) as h:
            header = pack_header(
                CctvHeader(resolution=RESOLUTION_V1080, length=50)
            )
            r1, w1 = await asyncio.open_connection("127.0.0.1", h.port)
            w1.write(header)  # header only; body withheld
            await w1.drain()
            await asyncio.sleep(0.4)  # let the body read time out

            # New connection is served normally (slot was released).
            await _send_and_close(h.port, _frame(RESOLUTION_V720P, b"after-timeout"))
            await asyncio.sleep(0.05)
            assert [b for b, _, _ in h.frames] == [b"after-timeout"]

            w1.close()
            try:
                await w1.wait_closed()
            except Exception:
                pass


class TestLengthEndianness:
    @pytest.mark.asyncio
    async def test_little_endian_length_is_accepted(self):
        # The AMR ships m_len as a raw little-endian uint32.  Reading it as
        # big-endian explodes (> max_body), so the server falls back to
        # little-endian and delivers the frame intact.
        body = b"\xFF\xD8" + b"x" * 4098 + b"\xFF\xD9"  # 4102 bytes
        header = RESOLUTION_V720P.encode("ascii") + len(body).to_bytes(4, "little")
        async with _ServerHarness() as h:
            await _send_and_close(h.port, header + body)
            await asyncio.sleep(0.1)
            assert [b for b, _, _ in h.frames] == [body]


class TestHandlerIsolation:
    @pytest.mark.asyncio
    async def test_handler_exception_does_not_kill_stream(self):
        async with _ServerHarness() as h:
            h.handler_should_raise = True
            r, w = await asyncio.open_connection("127.0.0.1", h.port)
            w.write(_frame(RESOLUTION_V1080, b"fail-me"))
            await w.drain()
            await asyncio.sleep(0.05)

            # Flip the flag and send another frame on the same connection.
            h.handler_should_raise = False
            w.write(_frame(RESOLUTION_V1080, b"ok"))
            await w.drain()
            await asyncio.sleep(0.1)

            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass

            assert len(h.frames) == 1
            assert h.frames[0][0] == b"ok"
