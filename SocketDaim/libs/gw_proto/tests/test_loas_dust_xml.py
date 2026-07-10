"""Tests for gw_proto.codec.loas.dust_xml.

Sample XML mirrors the body shown on pages 17 and 19 of the vendor PDF
(``SocketDaim/Tfoi v4a 분진센서 정합_r3.pdf``).
"""

from __future__ import annotations

import pytest

from gw_proto.codec.loas.dust_xml import DustInspectionPayload, parse_dust_inspection
from gw_proto.codec.loas.errors import XmlDecodeError, XmlParseError


# Corrected vendor spec (later revision): typos fixed
#   DUST_ALRAM           → DUST_ALARM
#   INSPECTION_LOACL_ID  → INSPECTION_LOCAL_ID
SAMPLE_XML = b"""<ELEMENT>
    <CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>
    <DUST_DATA>0.0400</DUST_DATA>
    <DUST_ALARM>3</DUST_ALARM>
    <DATETIME>2026-05-23 18:30:00.000000</DATETIME>
    <SENSOR_TYPE>3</SENSOR_TYPE>
    <SENSOR_INDEX>1</SENSOR_INDEX>
    <TARGET_INDEX>1</TARGET_INDEX>
    <WAYPOINT_X>12.345</WAYPOINT_X>
    <WAYPOINT_Y>67.890</WAYPOINT_Y>
    <WAYPOINT_Z>0.000</WAYPOINT_Z>
    <LOCATION_ID>1</LOCATION_ID>
    <MAP_ID>1</MAP_ID>
    <NAVIGATION_ID>1</NAVIGATION_ID>
    <EXEC_ID>1001</EXEC_ID>
    <PLANT_ID>1</PLANT_ID>
    <TARGET_ID>1</TARGET_ID>
    <UGV_ID>1</UGV_ID>
    <WAYPOINT_ID>1</WAYPOINT_ID>
    <INSPECTION_LOCAL_ID>1000001</INSPECTION_LOCAL_ID>
    <INSPECTION_PAN>0</INSPECTION_PAN>
    <INSPECTION_TILT>0</INSPECTION_TILT>
    <INSPECTION_LIFT>0</INSPECTION_LIFT>
    <OBJECT_ID>1</OBJECT_ID>
    <ROT_X>0.000</ROT_X>
    <ROT_Y>0.000</ROT_Y>
    <ROT_Z>0.000</ROT_Z>
    <ROT_W>1.000</ROT_W>
    <MISSION_ID>1734498123456</MISSION_ID>
</ELEMENT>"""

# Legacy typo form — kept so we can verify backward-compat fallback.
LEGACY_TYPO_XML = SAMPLE_XML \
    .replace(b"<DUST_ALARM>", b"<DUST_ALRAM>") \
    .replace(b"</DUST_ALARM>", b"</DUST_ALRAM>") \
    .replace(b"<INSPECTION_LOCAL_ID>", b"<INSPECTION_LOACL_ID>") \
    .replace(b"</INSPECTION_LOCAL_ID>", b"</INSPECTION_LOACL_ID>")


class TestParseSample:
    """Exactly the example from the corrected spec must round-trip cleanly."""

    @pytest.fixture
    def payload(self) -> DustInspectionPayload:
        return parse_dust_inspection(SAMPLE_XML)

    def test_cmd_id(self, payload):
        assert payload.cmd_id == "DUST_INSPECTION_INFOR"

    def test_measurement(self, payload):
        assert payload.dust_data == 0.04
        assert payload.dust_alarm == 3
        assert payload.datetime_str == "2026-05-23 18:30:00.000000"

    def test_waypoint_coordinates(self, payload):
        assert payload.waypoint_x == 12.345
        assert payload.waypoint_y == 67.89
        assert payload.waypoint_z == 0.0

    def test_quaternion(self, payload):
        assert payload.rot_x == 0.0
        assert payload.rot_y == 0.0
        assert payload.rot_z == 0.0
        assert payload.rot_w == 1.0

    def test_identifiers(self, payload):
        assert payload.ugv_id == 1
        assert payload.location_id == 1
        assert payload.map_id == 1
        assert payload.navigation_id == 1
        assert payload.exec_id == 1001
        assert payload.plant_id == 1
        assert payload.target_id == 1
        assert payload.waypoint_id == 1
        assert payload.object_id == 1
        assert payload.mission_id == 1734498123456

    def test_local_id(self, payload):
        """Corrected tag <INSPECTION_LOCAL_ID> maps to inspection_local_id."""
        assert payload.inspection_local_id == 1000001

    def test_pan_tilt_lift(self, payload):
        assert payload.inspection_pan == 0
        assert payload.inspection_tilt == 0
        assert payload.inspection_lift == 0


class TestLegacyTypoFallback:
    """Older firmware that still ships <DUST_ALRAM> / <INSPECTION_LOACL_ID>
    must still parse — the fallback path keeps a mixed fleet working."""

    def test_typo_payload_equivalent_to_corrected(self):
        legacy = parse_dust_inspection(LEGACY_TYPO_XML)
        corrected = parse_dust_inspection(SAMPLE_XML)
        assert legacy.dust_alarm == corrected.dust_alarm == 3
        assert legacy.inspection_local_id == corrected.inspection_local_id == 1000001
        # All other fields should also agree
        assert legacy == corrected

    def test_corrected_tag_wins_when_both_present(self):
        """If a unit somehow sends both, the corrected form is authoritative."""
        xml = (
            b"<ELEMENT>"
            b"<CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>"
            b"<DUST_ALARM>2</DUST_ALARM>"
            b"<DUST_ALRAM>0</DUST_ALRAM>"
            b"<INSPECTION_LOCAL_ID>42</INSPECTION_LOCAL_ID>"
            b"<INSPECTION_LOACL_ID>99</INSPECTION_LOACL_ID>"
            b"</ELEMENT>"
        )
        payload = parse_dust_inspection(xml)
        assert payload.dust_alarm == 2
        assert payload.inspection_local_id == 42


class TestForwardCompat:
    def test_unknown_tag_silently_ignored(self):
        xml = (
            b"<ELEMENT>"
            b"<CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>"
            b"<DUST_DATA>1.23</DUST_DATA>"
            b"<FUTURE_VENDOR_TAG>whatever</FUTURE_VENDOR_TAG>"
            b"</ELEMENT>"
        )
        payload = parse_dust_inspection(xml)
        assert payload.dust_data == 1.23

    def test_optional_fields_default_to_none(self):
        """A minimal frame should still parse; missing tags → None."""
        xml = (
            b"<ELEMENT><CMD_ID>DUST_INSPECTION_INFOR</CMD_ID></ELEMENT>"
        )
        payload = parse_dust_inspection(xml)
        assert payload.cmd_id == "DUST_INSPECTION_INFOR"
        assert payload.dust_data is None
        assert payload.dust_alarm is None
        assert payload.ugv_id is None

    def test_empty_tag_text_becomes_none(self):
        xml = (
            b"<ELEMENT>"
            b"<CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>"
            b"<DUST_DATA>   </DUST_DATA>"
            b"</ELEMENT>"
        )
        payload = parse_dust_inspection(xml)
        assert payload.dust_data is None


class TestRequiredFields:
    def test_missing_cmd_id_raises(self):
        xml = b"<ELEMENT><DUST_DATA>1.0</DUST_DATA></ELEMENT>"
        with pytest.raises(XmlParseError, match="CMD_ID"):
            parse_dust_inspection(xml)

    def test_unsupported_cmd_id_raises(self):
        xml = (
            b"<ELEMENT>"
            b"<CMD_ID>SOMETHING_NEW</CMD_ID>"
            b"</ELEMENT>"
        )
        with pytest.raises(XmlParseError, match="Unsupported CMD_ID"):
            parse_dust_inspection(xml)

    def test_wrong_root_element_raises(self):
        xml = (
            b"<NOT_ELEMENT>"
            b"<CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>"
            b"</NOT_ELEMENT>"
        )
        with pytest.raises(XmlParseError, match="root"):
            parse_dust_inspection(xml)


class TestMalformedInput:
    def test_malformed_xml_raises(self):
        with pytest.raises(XmlParseError, match="Malformed XML"):
            parse_dust_inspection(b"<ELEMENT><CMD_ID>")

    def test_non_numeric_in_numeric_field_raises(self):
        xml = (
            b"<ELEMENT>"
            b"<CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>"
            b"<DUST_DATA>not_a_number</DUST_DATA>"
            b"</ELEMENT>"
        )
        with pytest.raises(XmlParseError, match="DUST_DATA"):
            parse_dust_inspection(xml)


class TestEncoding:
    def test_utf8_korean_in_unknown_field(self):
        """Non-ASCII inside an ignored tag should still decode cleanly."""
        xml = (
            "<ELEMENT>"
            "<CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>"
            "<NOTE>한글주석</NOTE>"
            "</ELEMENT>"
        ).encode("utf-8")
        payload = parse_dust_inspection(xml)
        assert payload.cmd_id == "DUST_INSPECTION_INFOR"

    def test_euc_kr_fallback(self):
        """If the vendor ships EUC-KR-encoded XML, we fall back."""
        xml = (
            "<?xml version='1.0' encoding='euc-kr'?>"
            "<ELEMENT>"
            "<CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>"
            "<NOTE>한글</NOTE>"
            "</ELEMENT>"
        ).encode("euc-kr")
        # raw bytes are not valid UTF-8 (Korean syllable encodes differently),
        # so UTF-8 attempt fails and EUC-KR fallback kicks in.
        payload = parse_dust_inspection(xml)
        assert payload.cmd_id == "DUST_INSPECTION_INFOR"

    def test_undecodable_bytes_raises(self):
        """Bytes that are valid in neither encoding raise XmlDecodeError."""
        # 0x80 is a continuation byte invalid as a UTF-8 starter and a
        # control region byte in EUC-KR (lead bytes are 0xA1-0xFE).
        with pytest.raises(XmlDecodeError):
            parse_dust_inspection(b"\x80\x81\x82\x83")
