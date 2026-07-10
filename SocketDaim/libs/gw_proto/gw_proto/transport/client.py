"""TCP client wrapper for Egress Gateway and Mock sender."""

from __future__ import annotations

import asyncio
import logging
import time

from ..codec.base import Codec
from ..errors import ConnectionTimeout, GwProtoError
from ..framing import read_frame, write_frame
from ..messages import Message, MessageType

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL: float = 30.0

# Reconnection backoff constants (section 4.4)
BACKOFF_INITIAL: float = 1.0
BACKOFF_MULTIPLIER: float = 2.0
BACKOFF_MAX: float = 60.0


class TcpClient:
    """Async TCP client with automatic reconnection and heartbeat."""

    def __init__(self, host: str, port: int, codec: Codec) -> None:
        self._host = host
        self._port = port
        self._codec = codec
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._hb_task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Establish a TCP connection (with retry on failure)."""
        delay = BACKOFF_INITIAL
        while True:
            try:
                self._reader, self._writer = await asyncio.open_connection(
                    self._host, self._port
                )
                self._connected.set()
                logger.info("Connected to %s:%s", self._host, self._port)
                self._start_heartbeat()
                return
            except OSError as exc:
                logger.warning(
                    "Connection to %s:%s failed (%s), retrying in %.1fs",
                    self._host, self._port, exc, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * BACKOFF_MULTIPLIER, BACKOFF_MAX)

    async def close(self) -> None:
        """Gracefully close the connection."""
        self._stop_heartbeat()
        self._connected.clear()
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        logger.info("Disconnected from %s:%s", self._host, self._port)

    # -- send / receive ----------------------------------------------------

    async def send(self, message: Message) -> None:
        """Encode and send a message."""
        if self._writer is None or self._writer.is_closing():
            raise GwProtoError("Not connected")
        msg_type, payload = self._codec.encode(message)
        await write_frame(self._writer, msg_type, payload)

    async def receive(self) -> Message:
        """Read and decode one message from the server."""
        if self._reader is None:
            raise GwProtoError("Not connected")
        msg_type, payload = await read_frame(self._reader)
        return self._codec.decode(msg_type, payload)

    # -- reconnect ---------------------------------------------------------

    async def reconnect(self) -> None:
        """Close the current connection and re-establish with backoff."""
        await self.close()
        await self.connect()

    # -- heartbeat ---------------------------------------------------------

    def _start_heartbeat(self) -> None:
        self._stop_heartbeat()
        self._hb_task = asyncio.create_task(self._heartbeat_loop())

    def _stop_heartbeat(self) -> None:
        if self._hb_task is not None:
            self._hb_task.cancel()
            self._hb_task = None

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if self.is_connected:
                    hb = Message.heartbeat()
                    msg_type, payload = self._codec.encode(hb)
                    await write_frame(self._writer, msg_type, payload)  # type: ignore[arg-type]
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            logger.exception("Heartbeat loop error")
