"""Length-prefixed framing for the gateway protocol (section 4.1).

Wire format per frame::

    +------------------+------------------+---------------------+
    | 4 bytes          | 4 bytes          | N bytes             |
    | payload length   | message type     | payload             |
    | (uint32, BE)     | (uint32, BE)     |                     |
    +------------------+------------------+---------------------+
"""

from __future__ import annotations

import asyncio
import struct

from .errors import (
    ConnectionTimeout,
    FramingError,
    PayloadTooLarge,
    UnknownMessageType,
    MAX_PAYLOAD_SIZE,
)
from .messages import MessageType

HEADER_SIZE: int = 8
HEADER_STRUCT: struct.Struct = struct.Struct("!II")  # two uint32, big-endian

# Default timeouts (section 4.4)
READ_TIMEOUT: float = 60.0
WRITE_TIMEOUT: float = 30.0


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    timeout: float = READ_TIMEOUT,
) -> tuple[MessageType, bytes]:
    """Read one frame from *reader* and return ``(msg_type, payload)``."""
    try:
        header = await asyncio.wait_for(
            reader.readexactly(HEADER_SIZE), timeout=timeout
        )
    except asyncio.TimeoutError:
        raise ConnectionTimeout(f"Read header timed out after {timeout}s")
    except asyncio.IncompleteReadError as exc:
        raise FramingError(f"Connection closed during header read ({len(exc.partial)} bytes)") from exc

    payload_length, raw_type = HEADER_STRUCT.unpack(header)

    if payload_length > MAX_PAYLOAD_SIZE:
        raise PayloadTooLarge(
            f"Payload length {payload_length} exceeds max {MAX_PAYLOAD_SIZE}"
        )

    try:
        msg_type = MessageType(raw_type)
    except ValueError:
        raise UnknownMessageType(f"Unknown message type code: 0x{raw_type:04X}")

    if payload_length == 0:
        return msg_type, b""

    try:
        payload = await asyncio.wait_for(
            reader.readexactly(payload_length), timeout=timeout
        )
    except asyncio.TimeoutError:
        raise ConnectionTimeout(f"Read payload timed out after {timeout}s")
    except asyncio.IncompleteReadError as exc:
        raise FramingError(
            f"Connection closed during payload read "
            f"(expected {payload_length}, got {len(exc.partial)})"
        ) from exc

    return msg_type, payload


async def write_frame(
    writer: asyncio.StreamWriter,
    msg_type: MessageType,
    payload: bytes,
    *,
    timeout: float = WRITE_TIMEOUT,
) -> None:
    """Write one frame (header + payload) to *writer*."""
    header = HEADER_STRUCT.pack(len(payload), msg_type.value)
    writer.write(header + payload)
    try:
        await asyncio.wait_for(writer.drain(), timeout=timeout)
    except asyncio.TimeoutError:
        raise ConnectionTimeout(f"Write drain timed out after {timeout}s")
