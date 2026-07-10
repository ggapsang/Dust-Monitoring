"""gw_proto – Gateway Protocol library for SocketDaim.

Public API re-exports for convenient ``from gw_proto import ...`` usage.
"""

from .codec import Codec, StandardCodec, get_codec
from .errors import (
    CodecError,
    ConnectionTimeout,
    FramingError,
    GwProtoError,
    PayloadTooLarge,
    UnknownMessageType,
)
from .framing import read_frame, write_frame
from .messages import (
    Message,
    MessageType,
    SensorSamplePayload,
    VideoChunkMeta,
    build_video_chunk_payload,
    parse_video_chunk,
)
from .transport import SessionContext, TcpClient, TcpServer

__all__ = [
    # codec
    "Codec",
    "StandardCodec",
    "get_codec",
    # errors
    "GwProtoError",
    "FramingError",
    "PayloadTooLarge",
    "CodecError",
    "UnknownMessageType",
    "ConnectionTimeout",
    # framing
    "read_frame",
    "write_frame",
    # messages
    "Message",
    "MessageType",
    "VideoChunkMeta",
    "SensorSamplePayload",
    "parse_video_chunk",
    "build_video_chunk_payload",
    # transport
    "TcpServer",
    "TcpClient",
    "SessionContext",
]
