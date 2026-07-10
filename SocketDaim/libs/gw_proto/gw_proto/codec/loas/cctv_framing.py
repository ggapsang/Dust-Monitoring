"""CCTV 9-byte fixed-header framing (LOAS Tfoi v4a).

Header layout (big-endian where applicable)::

    offset  field    type        value
      0     m_type   char[5]     ASCII tag: V1080 / V720p / V640p
      5     m_len    uint32      JPEG body length (bytes)

There is no SOP magic number; the 5-byte resolution tag itself serves as
the frame discriminator.  The body is a raw JPEG (one full frame per
push) — no chunking, no metadata channel.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .constants import CCTV_HEADER_SIZE, CCTV_TAG_SIZE, RESOLUTION_TAGS
from .errors import LoasFramingError, UnknownResolutionError

_LEN_STRUCT: struct.Struct = struct.Struct("!I")  # big-endian uint32

assert CCTV_TAG_SIZE + _LEN_STRUCT.size == CCTV_HEADER_SIZE


@dataclass(slots=True, frozen=True)
class CctvHeader:
    resolution: str  # always one of RESOLUTION_TAGS once validated
    length: int


def pack_header(hdr: CctvHeader) -> bytes:
    """Serialize a :class:`CctvHeader` to its 9-byte wire form.

    Raises :class:`UnknownResolutionError` if the resolution string is not
    in :data:`RESOLUTION_TAGS`; raises :class:`LoasFramingError` if the
    length doesn't fit in a uint32.
    """
    if hdr.resolution not in RESOLUTION_TAGS:
        raise UnknownResolutionError(
            f"Unknown resolution tag {hdr.resolution!r}; "
            f"expected one of {sorted(RESOLUTION_TAGS)}"
        )
    tag = hdr.resolution.encode("ascii")
    if len(tag) != CCTV_TAG_SIZE:
        # Defensive — RESOLUTION_TAGS membership should already guarantee
        # this, but a developer adding a new tag of wrong width should hit
        # a clear error rather than corrupt the stream.
        raise LoasFramingError(
            f"Resolution tag must be {CCTV_TAG_SIZE} bytes, got {len(tag)}"
        )
    try:
        length_bytes = _LEN_STRUCT.pack(hdr.length)
    except struct.error as exc:
        raise LoasFramingError(f"Length out of uint32 range: {exc}") from exc
    return tag + length_bytes


def unpack_header(buf: bytes) -> CctvHeader:
    """Parse 9 bytes into a :class:`CctvHeader`.

    The 5-byte tag is validated against :data:`RESOLUTION_TAGS`.  This is the
    frame-sync guard: there is no SOP magic, so the resolution tag is the only
    thing that tells us we are aligned on a real frame boundary.  Loosening it
    to "any ASCII" would let desynced/garbage bytes masquerade as a header and
    silently corrupt the stream.  If a real AMR ships a tag outside our set,
    the server logs ``loas_cctv_header_rejected`` with the raw ``header_hex`` —
    add the observed tag here explicitly rather than disabling the check.

    Raises:
        LoasFramingError:        wrong header size, or tag bytes not ASCII.
        UnknownResolutionError:  tag not in :data:`RESOLUTION_TAGS`.
    """
    if len(buf) != CCTV_HEADER_SIZE:
        raise LoasFramingError(
            f"CCTV header expects {CCTV_HEADER_SIZE} bytes, got {len(buf)}"
        )
    try:
        tag = buf[:CCTV_TAG_SIZE].decode("ascii")
    except UnicodeDecodeError as exc:
        raise LoasFramingError(
            f"Resolution tag bytes are not ASCII: {buf[:CCTV_TAG_SIZE]!r}"
        ) from exc
    if tag not in RESOLUTION_TAGS:
        raise UnknownResolutionError(
            f"Unknown resolution tag {tag!r}; "
            f"expected one of {sorted(RESOLUTION_TAGS)}"
        )
    (length,) = _LEN_STRUCT.unpack(buf[CCTV_TAG_SIZE:CCTV_HEADER_SIZE])
    return CctvHeader(resolution=tag, length=length)
