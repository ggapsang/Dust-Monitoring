"""Message types and payload helpers for gw_proto."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class MessageType(IntEnum):
    """Wire-level message type codes (section 4.2)."""

    VIDEO_CHUNK = 0x0001
    VIDEO_COMPLETE = 0x0002
    SENSOR_SAMPLE = 0x0010
    ANALYSIS_RESULT = 0x0100
    ALERT = 0x0101
    HEARTBEAT = 0x0F00
    ACK = 0x0F01
    ERROR = 0x0FFF


@dataclass(slots=True)
class Message:
    """Application-level message exchanged over the gateway protocol."""

    msg_type: MessageType
    payload: bytes = b""
    metadata: dict[str, Any] | None = None

    # -- convenience constructors ------------------------------------------

    @classmethod
    def heartbeat(cls) -> Message:
        return cls(msg_type=MessageType.HEARTBEAT, payload=b"{}")

    @classmethod
    def ack(cls) -> Message:
        return cls(msg_type=MessageType.ACK, payload=b"{}")

    @classmethod
    def error(cls, reason: str) -> Message:
        body = json.dumps({"error": reason}).encode()
        return cls(msg_type=MessageType.ERROR, payload=body)


# -- Video Chunk helpers ---------------------------------------------------

@dataclass(slots=True)
class VideoChunkMeta:
    """JSON header embedded in a VIDEO_CHUNK payload.

    Wire identifier: ``station_name``.  UUIDs (`station_id`) are private
    to each module's database and never flow over the wire — different
    senders (real AMRs, mock_images, …) cannot be expected to share
    UUID values out-of-band.

    Optional fields are forwarded into the gateway's `video` table so
    downstream consumers can use them.  Senders that don't supply them
    leave the columns NULL.
    """

    video_id: str
    chunk_seq: int
    total_chunks: int
    station_name: str
    captured_at: str | None = None
    amr_id: str | None = None
    amr_position: dict[str, Any] | None = None
    source_format: str | None = None


def parse_video_chunk(payload: bytes) -> tuple[VideoChunkMeta, bytes]:
    """Split a VIDEO_CHUNK payload into (JSON metadata, binary body).

    Format: ``<JSON header>\\n<binary bytes>``
    """
    sep = payload.index(b"\n")
    header_json = payload[:sep]
    binary_body = payload[sep + 1 :]
    data = json.loads(header_json)
    name = data.get("station_name")
    if not name:
        raise KeyError("station_name")
    meta = VideoChunkMeta(
        video_id=data["video_id"],
        chunk_seq=data["chunk_seq"],
        total_chunks=data["total_chunks"],
        station_name=str(name),
        captured_at=data.get("captured_at"),
        amr_id=data.get("amr_id"),
        amr_position=data.get("amr_position"),
        source_format=data.get("source_format"),
    )
    return meta, binary_body


def build_video_chunk_payload(meta: VideoChunkMeta, binary_body: bytes) -> bytes:
    """Build a VIDEO_CHUNK payload from metadata and binary body."""
    header: dict[str, Any] = {
        "video_id": meta.video_id,
        "chunk_seq": meta.chunk_seq,
        "total_chunks": meta.total_chunks,
        "station_name": meta.station_name,
    }
    if meta.captured_at is not None:
        header["captured_at"] = meta.captured_at
    if meta.amr_id is not None:
        header["amr_id"] = meta.amr_id
    if meta.amr_position is not None:
        header["amr_position"] = meta.amr_position
    if meta.source_format is not None:
        header["source_format"] = meta.source_format
    return json.dumps(header).encode() + b"\n" + binary_body


# -- Sensor Sample helpers -------------------------------------------------

@dataclass(slots=True)
class SensorSamplePayload:
    """JSON payload for a SENSOR_SAMPLE message.

    Wire identifier is ``station_name``; see VideoChunkMeta for rationale.
    """

    station_name: str
    measurement_type: str
    value: float
    unit: str
    sampled_at: str
