"""LOAS Tfoi v4a protocol constants.

Source: ``SocketDaim/Tfoi v4a 분진센서 정합_r3.pdf`` (Sections 02-03, pages 13-20).

Two independent services share this codec package:

* **DUST** — 12-byte fixed header + XML body (max 1448 bytes).  Magic SOP
  ``0xAABB`` and DataObjectID ``0xD002`` (``DUST_INSPECTION_INFOR``).
* **CCTV** — 9-byte fixed header (5-byte resolution tag + ``uint32`` length)
  followed by a raw JPEG body.  No magic number; the resolution tag itself
  acts as discriminator.

바이트 순서: 벤더 스펙은 명시하지 않으나 실제 장비는 **리틀엔디안**으로 보낸다
(네트워크 표준 아님).  DUST 헤더는 실측 확인됨(sop=``bb aa``=0xAABB LE,
length=``e8 03``=1000 LE, IP 패킷 크기와 일치).  CCTV 의 ``m_len`` 은 벤더가
``htonl()`` 없이 raw ``uint32_t`` 로 보내므로 일반 HW 에서 리틀엔디안이며,
서버(loas_cctv_server)는 big-endian 으로 먼저 읽고 값이 비정상이면 little-endian
으로 폴백한다.
"""

from __future__ import annotations

# -- DUST (Dumo Pro dust sensor) ------------------------------------------

SOP_DUST: int = 0xAABB
"""Start-of-packet magic for DUST frames."""

DOID_DUST_INSPECTION: int = 0xD002
"""DataObjectID for ``DUST_INSPECTION_INFOR`` messages."""

PROTOCOL_VERSION: int = 0x02
"""Protocol version currently spec'd by the vendor."""

DUST_HEADER_SIZE: int = 12
"""Bytes: ``sop(2) + id(2) + ver(1) + enc(1) + ts(4) + length(2)``."""

DUST_MAX_BODY: int = 1448
"""Maximum XML body length per spec (1460-byte frame minus 12-byte header)."""

ENCRYPTION_NONE: int = 0
ENCRYPTION_ENABLED: int = 1


# -- CCTV (AMR camera) ----------------------------------------------------

CCTV_HEADER_SIZE: int = 9
"""Bytes: ``m_type[5] (ASCII) + m_len (uint32)``."""

CCTV_TAG_SIZE: int = 5
"""Width of the resolution tag field."""

RESOLUTION_V1080: str = "V1080"
RESOLUTION_V720P: str = "V720p"
RESOLUTION_V640P: str = "V640p"

RESOLUTION_TAGS: frozenset[str] = frozenset(
    {RESOLUTION_V1080, RESOLUTION_V720P, RESOLUTION_V640P}
)
"""Set of accepted 5-byte ASCII resolution tags."""

CCTV_MAX_BODY: int = 16 * 1024 * 1024
"""Sanity bound on a single JPEG body (bytes) — not a spec field.

A correctly framed 1080p/4K JPEG is at most a few MB.  A declared length far
above this means the stream is mis-framed (wrong byte order, desync), so the
listener drops the connection instead of trying to read it — which would
otherwise block on ``readexactly`` and pin the single-AMR slot indefinitely.
"""


# -- XML --------------------------------------------------------------------

CMD_ID_DUST_INSPECTION: str = "DUST_INSPECTION_INFOR"
"""Expected ``<CMD_ID>`` value for the only DataObjectID we currently parse."""

XML_PRIMARY_ENCODING: str = "utf-8"
XML_FALLBACK_ENCODING: str = "euc-kr"
