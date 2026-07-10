"""TCP listener for LOAS Tfoi v4a DUST frames.

One-way push protocol — the AMR sends, the Gateway receives, no ACK and
no heartbeat travel in either direction.  This server therefore strips
all of the bidirectional concerns that :class:`gw_proto.transport.server.TcpServer`
juggles (session registry, heartbeat sender, response writeback) and is
just a framing loop that calls a user-supplied async callback per frame.

Stream-sync invariants:

* If the 12-byte header validates → we read exactly ``hdr.length`` body
  bytes from the stream regardless of what the handler decides to do
  with them.  Skipping the read would desynchronise the connection.
* If validation fails → we close the connection.  Whatever the peer is
  shipping no longer matches the spec and there is no recovery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from ..codec.loas.constants import DUST_HEADER_SIZE
from ..codec.loas.dust_framing import (
    DustHeader,
    is_encrypted,
    unpack_header,
    validate_header,
)
from ..codec.loas.errors import LoasFramingError

logger = logging.getLogger(__name__)

# Callback signature: (header, body, peer) -> awaitable.  Returning None
# (or anything) is fine; the server ignores the result.
FrameHandler = Callable[
    [DustHeader, bytes, tuple[str, int]],
    Awaitable[Any],
]


class LoasDustTcpServer:
    """Receive-only DUST listener."""

    def __init__(self, host: str, port: int, on_frame: FrameHandler) -> None:
        self._host = host
        self._port = port
        self._on_frame = on_frame
        self._server: asyncio.Server | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Bind, then run until :meth:`stop` is called."""
        self._server = await asyncio.start_server(
            self._on_connect, self._host, self._port
        )
        sockets = self._server.sockets or ()
        logger.info(
            "loas_dust_listening",
            extra={"addrs": [s.getsockname() for s in sockets]},
        )
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("loas_dust_stopped")

    # -- per-connection ----------------------------------------------------

    async def _on_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer_info = writer.get_extra_info("peername") or ("unknown", 0)
        peer: tuple[str, int] = (peer_info[0], peer_info[1])
        logger.info("loas_dust_connected", extra={"peer": peer})

        try:
            while True:
                try:
                    header_buf = await reader.readexactly(DUST_HEADER_SIZE)
                except asyncio.IncompleteReadError:
                    # Peer closed gracefully (or mid-header).  Either way
                    # there is nothing more to do on this connection.
                    return

                try:
                    hdr = unpack_header(header_buf)
                    validate_header(hdr)
                except LoasFramingError as exc:
                    # Best-effort peek at a few extra bytes so we can see the
                    # actual wire shape when troubleshooting protocol mismatches.
                    extra_bytes = b""
                    try:
                        extra_bytes = await asyncio.wait_for(
                            reader.read(64), timeout=0.2,
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass
                    logger.warning(
                        "loas_dust_header_rejected hex_first12=%s extra_next=%s err=%s peer=%s",
                        header_buf.hex(), extra_bytes.hex(), str(exc), peer,
                    )
                    return  # close connection; stream is no longer trustworthy

                # Always drain the body — even an encrypted frame must be
                # consumed off the wire to keep subsequent frames aligned.
                try:
                    body = await reader.readexactly(hdr.length)
                except asyncio.IncompleteReadError as exc:
                    logger.warning(
                        "loas_dust_body_truncated",
                        extra={
                            "peer": peer,
                            "expected": hdr.length,
                            "got": len(exc.partial),
                        },
                    )
                    return

                if is_encrypted(hdr):
                    logger.warning(
                        "loas_dust_encrypted_frame_dropped",
                        extra={"peer": peer, "length": hdr.length},
                    )
                    continue

                try:
                    await self._on_frame(hdr, body, peer)
                except Exception:
                    # Handler errors are isolated: one bad frame must not
                    # take down the stream.
                    logger.exception("loas_dust_handler_failed")
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        except Exception:
            logger.exception("loas_dust_connection_error")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("loas_dust_disconnected", extra={"peer": peer})
