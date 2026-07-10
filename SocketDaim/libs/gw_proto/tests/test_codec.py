"""Tests for gw_proto.codec."""

import json

import pytest

from gw_proto.codec import StandardCodec, get_codec
from gw_proto.messages import (
    Message,
    MessageType,
    VideoChunkMeta,
    build_video_chunk_payload,
)


class TestGetCodec:
    def test_standard(self):
        codec = get_codec("standard")
        assert isinstance(codec, StandardCodec)

    def test_vendor_protocol_not_dispatched(self):
        """Vendor protocols (e.g. LOAS) don't fit the Codec interface and
        are wired directly in the application layer."""
        with pytest.raises(ValueError, match="non-Codec"):
            get_codec("loas")

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="non-Codec"):
            get_codec("nonexistent")


class TestStandardCodecEncode:
    def test_encode_returns_type_and_payload(self):
        codec = StandardCodec()
        msg = Message.heartbeat()
        msg_type, payload = codec.encode(msg)
        assert msg_type == MessageType.HEARTBEAT
        assert payload == b"{}"

    def test_encode_error_preserves_json(self):
        codec = StandardCodec()
        msg = Message.error("oops")
        _, payload = codec.encode(msg)
        body = json.loads(payload)
        assert body["error"] == "oops"


class TestStandardCodecDecode:
    def test_decode_json_control_message(self):
        codec = StandardCodec()
        payload = json.dumps({"error": "timeout"}).encode()
        msg = codec.decode(MessageType.ERROR, payload)
        assert msg.msg_type == MessageType.ERROR
        assert msg.metadata is not None
        assert msg.metadata["error"] == "timeout"

    def test_decode_heartbeat_empty(self):
        codec = StandardCodec()
        msg = codec.decode(MessageType.HEARTBEAT, b"{}")
        assert msg.msg_type == MessageType.HEARTBEAT
        assert msg.metadata == {}

    def test_decode_video_chunk(self):
        codec = StandardCodec()
        meta = VideoChunkMeta(
            video_id="v1", chunk_seq=0, total_chunks=3, station_name="s1"
        )
        binary = b"\xff" * 64
        payload = build_video_chunk_payload(meta, binary)

        msg = codec.decode(MessageType.VIDEO_CHUNK, payload)
        assert msg.msg_type == MessageType.VIDEO_CHUNK
        assert msg.metadata is not None
        assert msg.metadata["video_id"] == "v1"
        # Full payload preserved for handler-level parsing
        assert msg.payload == payload

    def test_decode_sensor_sample(self):
        codec = StandardCodec()
        payload = json.dumps({
            "station_name": "s1",
            "measurement_type": "temperature",
            "value": 25.3,
            "unit": "°C",
            "sampled_at": "2026-04-16T10:00:00Z",
        }).encode()
        msg = codec.decode(MessageType.SENSOR_SAMPLE, payload)
        assert msg.metadata is not None
        assert msg.metadata["station_name"] == "s1"
        assert msg.metadata["value"] == 25.3


class TestStandardCodecRoundtrip:
    def test_roundtrip_ack(self):
        codec = StandardCodec()
        original = Message.ack()
        msg_type, payload = codec.encode(original)
        decoded = codec.decode(msg_type, payload)
        assert decoded.msg_type == original.msg_type
        assert decoded.payload == original.payload

    def test_roundtrip_video_chunk(self):
        codec = StandardCodec()
        meta = VideoChunkMeta(
            video_id="abc", chunk_seq=1, total_chunks=10,
            station_name="st1", captured_at="2026-04-16T12:00:00Z",
        )
        binary = bytes(range(256))
        original = Message(
            msg_type=MessageType.VIDEO_CHUNK,
            payload=build_video_chunk_payload(meta, binary),
        )
        msg_type, payload = codec.encode(original)
        decoded = codec.decode(msg_type, payload)
        assert decoded.msg_type == MessageType.VIDEO_CHUNK
        assert decoded.payload == original.payload


