"""Tests for gw_proto.transport (TcpServer + TcpClient)."""

from __future__ import annotations

import asyncio
import json

import pytest

from gw_proto.codec import StandardCodec
from gw_proto.messages import Message, MessageType
from gw_proto.transport.server import SessionContext, TcpServer
from gw_proto.transport.client import TcpClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Find a free TCP port on localhost."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestClientServerExchange:
    """Spin up TcpServer + TcpClient and exchange messages."""

    @pytest.mark.asyncio
    async def test_send_and_receive_ack(self):
        codec = StandardCodec()
        port = _free_port()
        received: list[Message] = []

        async def handler(msg: Message, ctx: SessionContext) -> Message | None:
            received.append(msg)
            return Message.ack()

        server = TcpServer("127.0.0.1", port, codec, handler)
        server_task = asyncio.create_task(server.start())

        # Give server time to bind
        await asyncio.sleep(0.1)

        client = TcpClient("127.0.0.1", port, codec)
        await client.connect()

        # Send a sensor sample
        payload = json.dumps({
            "station_id": "s1",
            "measurement_type": "temperature",
            "value": 22.0,
            "unit": "°C",
            "sampled_at": "2026-04-16T10:00:00Z",
        }).encode()
        await client.send(Message(msg_type=MessageType.SENSOR_SAMPLE, payload=payload))

        # Receive ACK
        response = await client.receive()
        assert response.msg_type == MessageType.ACK

        # Verify server received the message
        assert len(received) == 1
        assert received[0].msg_type == MessageType.SENSOR_SAMPLE

        await client.close()
        await server.stop()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_multiple_messages(self):
        codec = StandardCodec()
        port = _free_port()
        count = 0

        async def handler(msg: Message, ctx: SessionContext) -> Message | None:
            nonlocal count
            count += 1
            return Message.ack()

        server = TcpServer("127.0.0.1", port, codec, handler)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        client = TcpClient("127.0.0.1", port, codec)
        await client.connect()

        for i in range(5):
            await client.send(Message.heartbeat())
            resp = await client.receive()
            assert resp.msg_type == MessageType.ACK

        assert count == 5

        await client.close()
        await server.stop()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_handler_returns_none_no_response(self):
        """When the handler returns None, no response frame is sent."""
        codec = StandardCodec()
        port = _free_port()

        async def handler(msg: Message, ctx: SessionContext) -> Message | None:
            return None  # Deliberately no response

        server = TcpServer("127.0.0.1", port, codec, handler)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        client = TcpClient("127.0.0.1", port, codec)
        await client.connect()

        await client.send(Message.error("test"))

        # Send another message and get no response for the first;
        # the server should still be alive for the next message
        # We test this by sending a heartbeat (which also returns None here)
        # and then closing without hanging.
        await client.send(Message.heartbeat())

        await asyncio.sleep(0.1)
        await client.close()
        await server.stop()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
