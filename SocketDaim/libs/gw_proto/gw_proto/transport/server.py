"""TCP server wrapper for Ingestion Gateway."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from ..codec.base import Codec
from ..errors import ConnectionTimeout, FramingError, GwProtoError
from ..framing import READ_TIMEOUT, read_frame, write_frame
from ..messages import Message, MessageType

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL: float = 30.0  # seconds between outgoing heartbeats
HEARTBEAT_TIMEOUT: float = READ_TIMEOUT  # max silence before dropping a peer


@dataclass(slots=True)
class SessionContext:
    """Per-connection context exposed to the message handler."""

    peer: tuple[str, int]
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    connected_at: float = field(default_factory=time.monotonic)


# Handler signature:  async (Message, SessionContext) -> Message | None
MessageHandler = Callable[
    [Message, SessionContext],
    Coroutine[Any, Any, Message | None],
]


class TcpServer:
    """Async TCP server that decodes incoming frames and dispatches them."""

    def __init__(
        self,
        host: str,
        port: int,
        codec: Codec,
        handler: MessageHandler,
    ) -> None:
        self._host = host
        self._port = port
        self._codec = codec
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._sessions: dict[str, SessionContext] = {}
        self._tasks: dict[str, set[asyncio.Task[None]]] = {}

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start listening and block until the server is closed."""
        self._server = await asyncio.start_server(
            self._on_connect, self._host, self._port
        )
        addrs = [s.getsockname() for s in self._server.sockets]
        logger.info("TcpServer listening on %s", addrs)
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Gracefully shut down: close all connections, then stop serving."""
        # Cancel per-session tasks
        for sid, tasks in list(self._tasks.items()):
            for t in tasks:
                t.cancel()
        # Close the server socket
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        # Close writers
        for ctx in list(self._sessions.values()):
            ctx.writer.close()
        self._sessions.clear()
        self._tasks.clear()
        logger.info("TcpServer stopped")

    # -- per-connection handling -------------------------------------------

    async def _on_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peername = writer.get_extra_info("peername") or ("unknown", 0)
        ctx = SessionContext(peer=(peername[0], peername[1]), reader=reader, writer=writer)
        self._sessions[ctx.session_id] = ctx
        self._tasks[ctx.session_id] = set()
        logger.info("New connection %s from %s", ctx.session_id[:8], ctx.peer)

        # Start heartbeat sender
        hb_task = asyncio.current_task()  # placeholder
        hb_task = asyncio.create_task(self._heartbeat_sender(ctx))
        self._tasks[ctx.session_id].add(hb_task)

        last_recv = time.monotonic()

        try:
            while True:
                # read_frame already enforces READ_TIMEOUT
                msg_type, payload = await read_frame(reader)
                last_recv = time.monotonic()

                message = self._codec.decode(msg_type, payload)

                response = await self._handler(message, ctx)
                if response is not None:
                    out_type, out_payload = self._codec.encode(response)
                    await write_frame(writer, out_type, out_payload)

        except ConnectionTimeout:
            logger.warning("Session %s timed out", ctx.session_id[:8])
        except FramingError as exc:
            logger.warning("Session %s framing error: %s", ctx.session_id[:8], exc)
        except GwProtoError as exc:
            logger.warning("Session %s proto error: %s", ctx.session_id[:8], exc)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        except Exception:
            logger.exception("Session %s unexpected error", ctx.session_id[:8])
        finally:
            self._cleanup_session(ctx)

    def _cleanup_session(self, ctx: SessionContext) -> None:
        for t in self._tasks.pop(ctx.session_id, set()):
            t.cancel()
        self._sessions.pop(ctx.session_id, None)
        ctx.writer.close()
        logger.info("Session %s closed", ctx.session_id[:8])

    # -- heartbeat ---------------------------------------------------------

    async def _heartbeat_sender(self, ctx: SessionContext) -> None:
        """Periodically send HEARTBEAT to the remote peer."""
        codec = self._codec
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                hb = Message.heartbeat()
                msg_type, payload = codec.encode(hb)
                await write_frame(ctx.writer, msg_type, payload)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            logger.exception("Heartbeat sender error for %s", ctx.session_id[:8])
