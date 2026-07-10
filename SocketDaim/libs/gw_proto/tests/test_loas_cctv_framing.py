"""Tests for gw_proto.codec.loas.cctv_framing."""

from __future__ import annotations

import pytest

from gw_proto.codec.loas.cctv_framing import (
    CctvHeader,
    pack_header,
    unpack_header,
)
from gw_proto.codec.loas.constants import (
    CCTV_HEADER_SIZE,
    RESOLUTION_V640P,
    RESOLUTION_V720P,
    RESOLUTION_V1080,
)
from gw_proto.codec.loas.errors import LoasFramingError, UnknownResolutionError


class TestPackHeader:
    @pytest.mark.parametrize(
        "resolution,expected_tag",
        [
            (RESOLUTION_V1080, b"V1080"),
            (RESOLUTION_V720P, b"V720p"),
            (RESOLUTION_V640P, b"V640p"),
        ],
    )
    def test_known_resolutions(self, resolution, expected_tag):
        hdr = CctvHeader(resolution=resolution, length=0x11223344)
        raw = pack_header(hdr)
        assert len(raw) == CCTV_HEADER_SIZE
        assert raw[:5] == expected_tag
        # uint32 big-endian length
        assert raw[5:9] == b"\x11\x22\x33\x44"

    def test_unknown_resolution_raises(self):
        with pytest.raises(UnknownResolutionError):
            pack_header(CctvHeader(resolution="V4321", length=10))

    def test_length_max_uint32_ok(self):
        hdr = CctvHeader(resolution=RESOLUTION_V1080, length=0xFFFFFFFF)
        raw = pack_header(hdr)
        assert raw[5:9] == b"\xFF\xFF\xFF\xFF"

    def test_length_overflow_raises(self):
        with pytest.raises(LoasFramingError, match="uint32"):
            pack_header(CctvHeader(resolution=RESOLUTION_V1080, length=2**32))

    def test_length_negative_raises(self):
        with pytest.raises(LoasFramingError, match="uint32"):
            pack_header(CctvHeader(resolution=RESOLUTION_V1080, length=-1))


class TestUnpackHeader:
    @pytest.mark.parametrize(
        "resolution", [RESOLUTION_V1080, RESOLUTION_V720P, RESOLUTION_V640P]
    )
    def test_round_trip(self, resolution):
        original = CctvHeader(resolution=resolution, length=4096)
        assert unpack_header(pack_header(original)) == original

    def test_wrong_buffer_size_raises(self):
        with pytest.raises(LoasFramingError, match="9 bytes"):
            unpack_header(b"V1080\x00\x00\x00")  # 8 bytes
        with pytest.raises(LoasFramingError, match="9 bytes"):
            unpack_header(b"V1080\x00\x00\x00\x00\x00")  # 10 bytes

    def test_unknown_resolution_raises(self):
        bad = b"VXXXX" + b"\x00\x00\x00\x00"
        with pytest.raises(UnknownResolutionError):
            unpack_header(bad)

    def test_non_ascii_tag_raises(self):
        bad = b"\xFF\xFF\xFF\xFF\xFF" + b"\x00\x00\x00\x00"
        with pytest.raises(LoasFramingError, match="ASCII"):
            unpack_header(bad)

    def test_zero_length_ok(self):
        """A zero-length frame is structurally valid; whether the body
        side accepts it is a separate concern."""
        hdr = CctvHeader(resolution=RESOLUTION_V720P, length=0)
        assert unpack_header(pack_header(hdr)) == hdr
