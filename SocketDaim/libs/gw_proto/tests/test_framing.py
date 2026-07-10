"""Tests for gw_proto.framing."""

import asyncio
import struct

import pytest

from gw_proto.errors import FramingError, PayloadTooLarge, UnknownMessageType, MAX_PAYLOAD_SIZE
from gw_proto.framing import HEADER_STRUCT, read_frame, write_frame
from gw_proto.messages import MessageType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_reader(data: bytes) -> asyncio.StreamReader:
    """Create a StreamReader pre-loaded with *data*."""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


async def _roundtrip(msg_type: MessageType, payload: bytes) -> tuple[MessageType, bytes]:
    """Write a frame into an in-memory stream, then read it back."""
    reader = asyncio.StreamReader()
    # Dummy transport so StreamWriter doesn't complain
    transport = asyncio.get_event_loop()  # not used; we override drain
    protocol = asyncio.StreamReaderProtocol(reader)

    # Create a pair of connected sockets for the roundtrip
    server_ready = asyncio.Event()
    result: tuple[MessageType, bytes] | None = None

    async def _server(r: asyncio.StreamReader, w: asyncio.StreamWriter):
        nonlocal result
        result = await read_frame(r)
        w.close()

    srv = await asyncio.start_server(_server, "127.0.0.1", 0)
    addr = srv.sockets[0].getsockname()

    r, w = await asyncio.open_connection(addr[0], addr[1])
    await write_frame(w, msg_type, payload)
    w.close()
    await w.wait_closed()

    # Wait for server to finish
    srv.close()
    await srv.wait_closed()
    # small delay for server coroutine
    await asyncio.sleep(0.05)

    assert result is not None
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReadFrame:
    @pytest.mark.asyncio
    async def test_heartbeat_empty_payload(self):
        payload = b""
        header = HEADER_STRUCT.pack(len(payload), MessageType.HEARTBEAT.value)
        reader = _make_reader(header + payload)
        msg_type, data = await read_frame(reader)
        assert msg_type == MessageType.HEARTBEAT
        assert data == b""

    @pytest.mark.asyncio
    async def test_json_payload(self):
        payload = b'{"station_id":"s1"}'
        header = HEADER_STRUCT.pack(len(payload), MessageType.SENSOR_SAMPLE.value)
        reader = _make_reader(header + payload)
        msg_type, data = await read_frame(reader)
        assert msg_type == MessageType.SENSOR_SAMPLE
        assert data == payload

    @pytest.mark.asyncio
    async def test_payload_too_large(self):
        header = HEADER_STRUCT.pack(MAX_PAYLOAD_SIZE + 1, MessageType.VIDEO_CHUNK.value)
        reader = _make_reader(header)
        with pytest.raises(PayloadTooLarge):
            await read_frame(reader)

    @pytest.mark.asyncio
    async def test_unknown_message_type(self):
        header = HEADER_STRUCT.pack(0, 0xDEAD)
        reader = _make_reader(header)
        with pytest.raises(UnknownMessageType):
            await read_frame(reader)

    @pytest.mark.asyncio
    async def test_truncated_header(self):
        reader = _make_reader(b"\x00\x00")  # only 2 bytes
        with pytest.raises(FramingError):
            await read_frame(reader)

    @pytest.mark.asyncio
    async def test_truncated_payload(self):
        # Claim 100 bytes of payload but only provide 10
        header = HEADER_STRUCT.pack(100, MessageType.ACK.value)
        reader = _make_reader(header + b"x" * 10)
        with pytest.raises(FramingError):
            await read_frame(reader)


class TestRoundtrip:
    @pytest.mark.asyncio
    async def test_roundtrip_small_payload(self):
        msg_type, data = await _roundtrip(MessageType.ACK, b'{"ok":true}')
        assert msg_type == MessageType.ACK
        assert data == b'{"ok":true}'

    @pytest.mark.asyncio
    async def test_roundtrip_binary_payload(self):
        binary = bytes(range(256)) * 4
        msg_type, data = await _roundtrip(MessageType.VIDEO_CHUNK, binary)
        assert msg_type == MessageType.VIDEO_CHUNK
        assert data == binary

    @pytest.mark.asyncio
    async def test_roundtrip_empty_payload(self):
        msg_type, data = await _roundtrip(MessageType.HEARTBEAT, b"")
        assert msg_type == MessageType.HEARTBEAT
        assert data == b""
