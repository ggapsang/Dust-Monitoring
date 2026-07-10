"""TCP listener for LOAS Tfoi v4a CCTV frames.

One-way push protocol — no ACK, no heartbeat.  Frame layout is a 9-byte
header (5-byte resolution tag + uint32 length) followed by a raw JPEG.

Connection policy:
    Accept any number of concurrent connections from the AMR side.  The
    real AMR sender opens a fresh TCP connection per frame (connect →
    9-byte header → JPEG body → close), so the listener must not interfere
    with the previous connection when a new one arrives — doing so would
    RST a connection that is still in the middle of sending its body.

    Earlier revisions tracked a single "active slot" and either rejected
    a second concurrent connection or preempted the previous one by closing
    its writer.  Both behaviours caused observable frame loss in the field:
    the AMR was either FIN'd at accept time, or its in-flight frame was
    RST'd the moment the next frame's connection landed.  This revision
    removes the slot entirely and treats every connection independently —
    same as the DUST listener.

    ``hdr.length`` is bounded by ``max_body_bytes`` and the body read is
    wrapped in a timeout, so a single malformed or stalled frame cannot
    pin the connection indefinitely.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from ..codec.loas.cctv_framing import CctvHeader, unpack_header
from ..codec.loas.constants import CCTV_HEADER_SIZE, CCTV_MAX_BODY, CCTV_TAG_SIZE
from ..codec.loas.errors import LoasFramingError

logger = logging.getLogger(__name__)

# Callback signature: (jpg_body, resolution, peer) -> awaitable.
FrameHandler = Callable[
    [bytes, str, tuple[str, int]],
    Awaitable[Any],
]

# A JPEG body should arrive promptly once its header has been read; if it
# does not, we drop the connection rather than block the single-AMR slot.
DEFAULT_BODY_READ_TIMEOUT = 30.0


class LoasCctvTcpServer:
    """Receive-only CCTV listener; accepts each connection independently."""

    def __init__(
        self,
        host: str,
        port: int,
        on_frame: FrameHandler,
        *,
        max_body_bytes: int = CCTV_MAX_BODY,
        body_read_timeout: float = DEFAULT_BODY_READ_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._on_frame = on_frame
        self._max_body = max_body_bytes
        self._body_timeout = body_read_timeout
        self._server: asyncio.Server | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._on_connect, self._host, self._port
        )
        sockets = self._server.sockets or ()
        logger.info(
            "loas_cctv_listening",
            extra={"addrs": [s.getsockname() for s in sockets]},
        )
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("loas_cctv_stopped")

    # -- per-connection ----------------------------------------------------

    async def _on_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer_info = writer.get_extra_info("peername") or ("unknown", 0)
        peer: tuple[str, int] = (peer_info[0], peer_info[1])

        # No single-slot tracking.  Every connection is handled in its own
        # coroutine and only owns its own writer.  This matches the AMR's
        # observed behaviour of opening one fresh connection per frame.
        logger.info("loas_cctv_connected", extra={"peer": peer})
        frames_received = 0
        exit_reason = "unknown"
        try:
            while True:
                try:
                    header_buf = await reader.readexactly(CCTV_HEADER_SIZE)
                except asyncio.IncompleteReadError as exc:
                    # EOF on header read = peer closed cleanly between frames.
                    # Log so docker logs can distinguish this from other return
                    # paths (framing error, body timeout, cancellation).
                    exit_reason = (
                        "peer_eof_clean"
                        if len(exc.partial) == 0
                        else f"peer_eof_partial_header_{len(exc.partial)}"
                    )
                    return

                try:
                    hdr: CctvHeader = unpack_header(header_buf)
                except LoasFramingError as exc:
                    logger.warning(
                        "loas_cctv_header_rejected",
                        extra={
                            "peer": peer,
                            "err": str(exc),
                            "header_hex": header_buf.hex(),
                        },
                    )
                    exit_reason = "header_rejected"
                    return  # stream is no longer trustworthy

                # m_len byte order is not pinned by the vendor spec for CCTV:
                # it is shown as a raw C++ ``uint32_t`` with no htonl().  We read
                # network/big-endian first (consistent with the DUST half of the
                # same protocol, which works), but an embedded little-endian
                # sender would make that read explode.  When big-endian is
                # implausibly large, fall back to little-endian before giving up.
                length = hdr.length
                if length > self._max_body:
                    length_le = int.from_bytes(
                        header_buf[CCTV_TAG_SIZE:CCTV_HEADER_SIZE], "little"
                    )
                    if length_le <= self._max_body:
                        logger.warning(
                            "loas_cctv_length_little_endian",
                            extra={
                                "peer": peer,
                                "be": length,
                                "le": length_le,
                                "header_hex": header_buf.hex(),
                            },
                        )
                        length = length_le
                    else:
                        # Both byte orders are absurd → genuinely mis-framed.
                        logger.warning(
                            "loas_cctv_frame_too_large",
                            extra={
                                "peer": peer,
                                "be": length,
                                "le": length_le,
                                "max": self._max_body,
                                "header_hex": header_buf.hex(),
                            },
                        )
                        exit_reason = "frame_too_large"
                        return

                try:
                    body = await asyncio.wait_for(
                        reader.readexactly(length),
                        timeout=self._body_timeout,
                    )
                except asyncio.IncompleteReadError as exc:
                    logger.warning(
                        "loas_cctv_body_truncated",
                        extra={
                            "peer": peer,
                            "expected": length,
                            "got": len(exc.partial),
                            "header_hex": header_buf.hex(),
                        },
                    )
                    exit_reason = "body_truncated"
                    return
                except asyncio.TimeoutError:
                    logger.warning(
                        "loas_cctv_body_timeout",
                        extra={
                            "peer": peer,
                            "expected": length,
                            "header_hex": header_buf.hex(),
                        },
                    )
                    exit_reason = "body_timeout"
                    return

                try:
                    await self._on_frame(body, hdr.resolution, peer)
                except Exception:
                    logger.exception("loas_cctv_handler_failed")

                frames_received += 1
        except ConnectionResetError:
            exit_reason = "connection_reset"
        except BrokenPipeError:
            exit_reason = "broken_pipe"
        except asyncio.CancelledError:
            exit_reason = "cancelled"
            raise
        except Exception:
            exit_reason = "unhandled_exception"
            logger.exception("loas_cctv_connection_error")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.info(
                "loas_cctv_disconnected",
                extra={
                    "peer": peer,
                    "frames_received": frames_received,
                    "exit_reason": exit_reason,
                },
            )
