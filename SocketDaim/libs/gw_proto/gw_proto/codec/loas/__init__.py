"""LOAS Tfoi v4a vendor protocol package.

The LOAS protocol does *not* use the :class:`gw_proto.codec.Codec`
interface — DUST and CCTV have different framing widths (12 vs 9 bytes),
different body encodings (XML vs raw JPEG) and no shared message-type
discriminator.  Each service is therefore handled by its own framing
module and a dedicated transport in the upper layers.
"""

from __future__ import annotations

from . import constants
from .cctv_framing import CctvHeader, pack_header as pack_cctv_header
from .cctv_framing import unpack_header as unpack_cctv_header
from .dust_framing import (
    DustHeader,
    is_encrypted,
    pack_header as pack_dust_header,
    unpack_header as unpack_dust_header,
    validate_header as validate_dust_header,
)
from .dust_xml import DustInspectionPayload, decode_xml, parse_dust_inspection
from .errors import (
    EncryptedFrameError,
    InvalidSopError,
    LoasFramingError,
    LoasPayloadTooLargeError,
    LoasXmlError,
    UnknownResolutionError,
    UnsupportedDataObjectIdError,
    UnsupportedVersionError,
    XmlDecodeError,
    XmlParseError,
)

__all__ = [
    # constants module re-exposed for convenience: gw_proto.codec.loas.constants.*
    "constants",
    # DUST
    "DustHeader",
    "DustInspectionPayload",
    "decode_xml",
    "pack_dust_header",
    "unpack_dust_header",
    "validate_dust_header",
    "is_encrypted",
    "parse_dust_inspection",
    # CCTV
    "CctvHeader",
    "pack_cctv_header",
    "unpack_cctv_header",
    # errors
    "LoasFramingError",
    "LoasXmlError",
    "InvalidSopError",
    "UnsupportedDataObjectIdError",
    "UnsupportedVersionError",
    "LoasPayloadTooLargeError",
    "EncryptedFrameError",
    "UnknownResolutionError",
    "XmlDecodeError",
    "XmlParseError",
]
