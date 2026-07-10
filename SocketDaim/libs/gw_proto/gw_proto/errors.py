"""gw_proto error hierarchy."""

# 512 MB – maximum payload size per frame (section 4.1)
MAX_PAYLOAD_SIZE: int = 512 * 1024 * 1024


class GwProtoError(Exception):
    """Base exception for gw_proto."""


class FramingError(GwProtoError):
    """Malformed frame (bad header, truncated read, etc.)."""


class PayloadTooLarge(FramingError):
    """Payload length exceeds MAX_PAYLOAD_SIZE."""


class CodecError(GwProtoError):
    """Encode / decode failure."""


class UnknownMessageType(CodecError):
    """Unrecognised message type code."""


class ConnectionTimeout(GwProtoError):
    """Read or write timeout exceeded."""
