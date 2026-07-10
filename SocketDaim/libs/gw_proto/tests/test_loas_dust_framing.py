"""Tests for gw_proto.codec.loas.dust_framing."""

from __future__ import annotations

import pytest

from gw_proto.codec.loas.constants import (
    DOID_DUST_INSPECTION,
    DUST_HEADER_SIZE,
    DUST_MAX_BODY,
    PROTOCOL_VERSION,
    SOP_DUST,
)
from gw_proto.codec.loas.dust_framing import (
    DustHeader,
    is_encrypted,
    pack_header,
    unpack_header,
    validate_header,
)
from gw_proto.codec.loas.errors import (
    InvalidSopError,
    LoasFramingError,
    LoasPayloadTooLargeError,
    UnsupportedDataObjectIdError,
    UnsupportedVersionError,
)


def _good_header(**overrides) -> DustHeader:
    base = dict(
        sop=SOP_DUST,
        data_object_id=DOID_DUST_INSPECTION,
        version=PROTOCOL_VERSION,
        encryption=0,
        timestamp=1_700_000_000,
        length=128,
    )
    base.update(overrides)
    return DustHeader(**base)


class TestPackUnpack:
    def test_round_trip(self):
        hdr = _good_header()
        round_tripped = unpack_header(pack_header(hdr))
        assert round_tripped == hdr

    def test_wire_byte_layout(self):
        """Confirm field offsets + byte order match the device (little-endian).

        실제 장비 캡처로 확인: sop 가 와이어상 ``bb aa`` (=0xAABB LE) 등.
        """
        hdr = _good_header(
            timestamp=0x11223344,
            length=0x05A0,  # 1440
        )
        raw = pack_header(hdr)
        assert len(raw) == DUST_HEADER_SIZE
        # sop (0xAABB) little-endian at [0:2]  → on wire: bb aa
        assert raw[0:2] == b"\xBB\xAA"
        # data_object_id (0xD002) little-endian at [2:4] → 02 d0
        assert raw[2:4] == b"\x02\xD0"
        # version, encryption
        assert raw[4] == PROTOCOL_VERSION
        assert raw[5] == 0
        # timestamp little-endian uint32 at [6:10]
        assert raw[6:10] == b"\x44\x33\x22\x11"
        # length little-endian uint16 at [10:12]
        assert raw[10:12] == b"\xA0\x05"

    def test_encryption_flag_preserved(self):
        for flag in (0, 1):
            hdr = _good_header(encryption=flag)
            assert unpack_header(pack_header(hdr)).encryption == flag

    def test_unpack_wrong_length_raises(self):
        with pytest.raises(LoasFramingError, match="12 bytes"):
            unpack_header(b"\xAA\xBB")
        with pytest.raises(LoasFramingError):
            unpack_header(b"\x00" * 13)


class TestValidateHeader:
    def test_valid_header_passes(self):
        validate_header(_good_header())  # should not raise

    def test_wrong_sop(self):
        with pytest.raises(InvalidSopError):
            validate_header(_good_header(sop=0x1234))

    def test_unsupported_data_object_id(self):
        with pytest.raises(UnsupportedDataObjectIdError):
            validate_header(_good_header(data_object_id=0xFFFF))

    def test_unsupported_version(self):
        with pytest.raises(UnsupportedVersionError):
            validate_header(_good_header(version=0x01))

    def test_length_at_max_passes(self):
        validate_header(_good_header(length=DUST_MAX_BODY))

    def test_length_over_max_raises(self):
        with pytest.raises(LoasPayloadTooLargeError):
            validate_header(_good_header(length=DUST_MAX_BODY + 1))

    def test_validate_ignores_encryption_flag(self):
        """validate_header() must not raise on encryption=1; the caller
        decides whether to drop or attempt decryption."""
        validate_header(_good_header(encryption=1))


class TestIsEncrypted:
    def test_plain(self):
        assert is_encrypted(_good_header(encryption=0)) is False

    def test_encrypted(self):
        assert is_encrypted(_good_header(encryption=1)) is True
