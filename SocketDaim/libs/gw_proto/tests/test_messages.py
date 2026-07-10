"""Tests for gw_proto.messages."""

import json

from gw_proto.messages import (
    Message,
    MessageType,
    SensorSamplePayload,
    VideoChunkMeta,
    build_video_chunk_payload,
    parse_video_chunk,
)


class TestMessageType:
    def test_type_codes(self):
        assert MessageType.VIDEO_CHUNK == 0x0001
        assert MessageType.VIDEO_COMPLETE == 0x0002
        assert MessageType.SENSOR_SAMPLE == 0x0010
        assert MessageType.ANALYSIS_RESULT == 0x0100
        assert MessageType.ALERT == 0x0101
        assert MessageType.HEARTBEAT == 0x0F00
        assert MessageType.ACK == 0x0F01
        assert MessageType.ERROR == 0x0FFF

    def test_all_types_are_unique(self):
        values = [m.value for m in MessageType]
        assert len(values) == len(set(values))


class TestMessageHelpers:
    def test_heartbeat(self):
        msg = Message.heartbeat()
        assert msg.msg_type == MessageType.HEARTBEAT
        assert msg.payload == b"{}"

    def test_ack(self):
        msg = Message.ack()
        assert msg.msg_type == MessageType.ACK
        assert msg.payload == b"{}"

    def test_error(self):
        msg = Message.error("something broke")
        assert msg.msg_type == MessageType.ERROR
        body = json.loads(msg.payload)
        assert body["error"] == "something broke"


class TestVideoChunk:
    def test_parse_and_build_roundtrip(self):
        meta = VideoChunkMeta(
            video_id="abc-123",
            chunk_seq=2,
            total_chunks=5,
            station_name="station-A",
            captured_at="2026-04-16T10:00:00Z",
        )
        binary = b"\x00\x01\x02\x03" * 100

        payload = build_video_chunk_payload(meta, binary)
        parsed_meta, parsed_binary = parse_video_chunk(payload)

        assert parsed_meta.video_id == meta.video_id
        assert parsed_meta.chunk_seq == meta.chunk_seq
        assert parsed_meta.total_chunks == meta.total_chunks
        assert parsed_meta.station_name == meta.station_name
        assert parsed_meta.captured_at == meta.captured_at
        assert parsed_binary == binary

    def test_parse_without_captured_at(self):
        meta = VideoChunkMeta(
            video_id="xyz", chunk_seq=0, total_chunks=1, station_name="s1"
        )
        payload = build_video_chunk_payload(meta, b"data")
        parsed_meta, _ = parse_video_chunk(payload)
        assert parsed_meta.captured_at is None

    def test_binary_body_may_contain_newlines(self):
        meta = VideoChunkMeta(
            video_id="v1", chunk_seq=0, total_chunks=1, station_name="s1"
        )
        binary = b"line1\nline2\nline3"
        payload = build_video_chunk_payload(meta, binary)
        _, parsed_binary = parse_video_chunk(payload)
        assert parsed_binary == binary


class TestSensorSamplePayload:
    def test_construction(self):
        sp = SensorSamplePayload(
            station_name="s1",
            measurement_type="temperature",
            value=23.5,
            unit="°C",
            sampled_at="2026-04-16T10:00:00Z",
        )
        assert sp.station_name == "s1"
        assert sp.value == 23.5
