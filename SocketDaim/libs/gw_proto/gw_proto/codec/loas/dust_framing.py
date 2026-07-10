"""DUST 12-byte fixed-header framing (LOAS Tfoi v4a).

Header layout (**little-endian** — vendor empirical, NOT network standard)::

    offset  field        type     value
      0     sop          uint16   0xAABB  → on wire: bb aa
      2     id           uint16   0xD002  → on wire: 02 d0
      4     ver          uint8    0x02
      5     encryption   uint8    0 = plain, 1 = encrypted (we drop)
      6     timestamp    uint32   UTC epoch seconds (little-endian)
      10    length       uint16   body length, <= 1448 (little-endian)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .constants import (
    DOID_DUST_INSPECTION,
    DUST_HEADER_SIZE,
    DUST_MAX_BODY,
    ENCRYPTION_NONE,
    PROTOCOL_VERSION,
    SOP_DUST,
)
from .errors import (
    InvalidSopError,
    LoasFramingError,
    LoasPayloadTooLargeError,
    UnsupportedDataObjectIdError,
    UnsupportedVersionError,
)

_HEADER_STRUCT: struct.Struct = struct.Struct("<HHBBIH")
"""sop, id, ver, encryption, timestamp, length — all **little-endian**.

벤더 스펙(Tfoi v4a)은 바이트 순서를 명시하지 않으나, 실제 장비 캡처에서
sop 가 와이어상 ``bb aa`` (=0xAABB LE), length 가 ``e8 03`` (=1000 LE, IP 패킷
크기와 일치)로 확인됨 → 리틀엔디안.  네트워크 표준(big-endian)이 아님에 주의."""

assert _HEADER_STRUCT.size == DUST_HEADER_SIZE


@dataclass(slots=True, frozen=True)
class DustHeader:
    sop: int
    data_object_id: int
    version: int
    encryption: int
    timestamp: int
    length: int


def pack_header(hdr: DustHeader) -> bytes:
    """Serialize a :class:`DustHeader` to its 12-byte wire form.

    The caller is responsible for supplying valid field widths.  Use
    :func:`validate_header` first if you constructed the header from
    untrusted input.
    """
    return _HEADER_STRUCT.pack(
        hdr.sop,
        hdr.data_object_id,
        hdr.version,
        hdr.encryption,
        hdr.timestamp,
        hdr.length,
    )


def unpack_header(buf: bytes) -> DustHeader:
    """Parse 12 bytes into a :class:`DustHeader`.

    Raises :class:`LoasFramingError` if *buf* is the wrong size.  Field
    semantics (SOP, version, length bound, encryption) are checked
    separately by :func:`validate_header`.
    """
    if len(buf) != DUST_HEADER_SIZE:
        raise LoasFramingError(
            f"DUST header expects {DUST_HEADER_SIZE} bytes, got {len(buf)}"
        )
    sop, doid, ver, enc, ts, length = _HEADER_STRUCT.unpack(buf)
    return DustHeader(
        sop=sop,
        data_object_id=doid,
        version=ver,
        encryption=enc,
        timestamp=ts,
        length=length,
    )


def validate_header(hdr: DustHeader) -> None:
    """Reject frames whose fields contradict the spec.

    ``encryption=1`` is **not** checked here — the caller may want to read
    and drop the body to stay in stream sync before raising.  Use
    :func:`is_encrypted` for that branch.
    """
    if hdr.sop != SOP_DUST:
        raise InvalidSopError(
            f"Expected SOP 0x{SOP_DUST:04X}, got 0x{hdr.sop:04X}"
        )
    if hdr.data_object_id != DOID_DUST_INSPECTION:
        raise UnsupportedDataObjectIdError(
            f"Unsupported DataObjectID 0x{hdr.data_object_id:04X}"
        )
    if hdr.version != PROTOCOL_VERSION:
        raise UnsupportedVersionError(
            f"Unsupported protocol version 0x{hdr.version:02X}"
        )
    if hdr.length > DUST_MAX_BODY:
        raise LoasPayloadTooLargeError(
            f"Body length {hdr.length} exceeds max {DUST_MAX_BODY}"
        )


def is_encrypted(hdr: DustHeader) -> bool:
    """``True`` if the body bytes are encrypted (and thus unparseable)."""
    return hdr.encryption != ENCRYPTION_NONE
