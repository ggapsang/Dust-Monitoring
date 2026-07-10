"""LOAS-specific exception hierarchy.

All errors derive from :class:`gw_proto.errors.FramingError` or
:class:`gw_proto.errors.CodecError` so existing handlers that already catch
those base types continue to work without modification.
"""

from __future__ import annotations

from ...errors import CodecError, FramingError


class LoasFramingError(FramingError):
    """Base for malformed LOAS frame errors."""


class InvalidSopError(LoasFramingError):
    """DUST frame did not start with ``0xAABB``."""


class UnsupportedDataObjectIdError(LoasFramingError):
    """DUST frame's ``id`` field is not one we know how to parse."""


class UnsupportedVersionError(LoasFramingError):
    """DUST frame's ``ver`` field is not :data:`PROTOCOL_VERSION`."""


class LoasPayloadTooLargeError(LoasFramingError):
    """DUST body length exceeds :data:`DUST_MAX_BODY`."""


class EncryptedFrameError(LoasFramingError):
    """``encryption=1`` frame received; we have no key to decrypt it.

    Per the agreed policy the caller logs and drops the frame, then keeps
    the connection alive for the next frame.
    """


class UnknownResolutionError(LoasFramingError):
    """CCTV frame carried a 5-byte tag that is not in
    :data:`RESOLUTION_TAGS`."""


class LoasXmlError(CodecError):
    """Base for XML parsing failures inside a DUST body."""


class XmlDecodeError(LoasXmlError):
    """XML bytes could not be decoded in any supported encoding."""


class XmlParseError(LoasXmlError):
    """XML was decoded but failed structural / required-field validation."""
