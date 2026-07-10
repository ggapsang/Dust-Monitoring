"""DUST XML body parsing.

The vendor sends a single ``<ELEMENT>`` block per frame; the ``CMD_ID`` tag
identifies the message type.  Only ``DUST_INSPECTION_INFOR`` is currently
spec'd.

Vendor spec history: an earlier revision shipped two typos
(``<INSPECTION_LOACL_ID>``, ``<DUST_ALRAM>``).  These were corrected to
``<INSPECTION_LOCAL_ID>`` / ``<DUST_ALARM>`` in a later revision.  This
parser accepts the corrected names as the primary form and falls back to
the legacy typos so the gateway keeps working against any unit that
still ships the old firmware.

Unknown tags inside ``<ELEMENT>`` are silently ignored to keep forward
compatibility with vendor additions.  Required tags missing → raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar
from xml.etree import ElementTree as ET

from .constants import (
    CMD_ID_DUST_INSPECTION,
    XML_FALLBACK_ENCODING,
    XML_PRIMARY_ENCODING,
)
from .errors import XmlDecodeError, XmlParseError

_T = TypeVar("_T")


@dataclass(slots=True, frozen=True)
class DustInspectionPayload:
    """Decoded ``DUST_INSPECTION_INFOR`` element.

    ``cmd_id`` is the only field guaranteed to be non-None — the vendor may
    omit any other tag in a future spec revision, and we should not crash
    the listener for that.  Storage/handler code decides what to do with
    nulls.
    """

    cmd_id: str
    dust_data: float | None = None
    dust_alarm: int | None = None
    datetime_str: str | None = None

    sensor_type: int | None = None
    sensor_index: int | None = None
    target_index: int | None = None

    waypoint_x: float | None = None
    waypoint_y: float | None = None
    waypoint_z: float | None = None

    rot_x: float | None = None
    rot_y: float | None = None
    rot_z: float | None = None
    rot_w: float | None = None

    ugv_id: int | None = None
    location_id: int | None = None
    map_id: int | None = None
    navigation_id: int | None = None
    exec_id: int | None = None
    plant_id: int | None = None
    target_id: int | None = None
    waypoint_id: int | None = None
    inspection_local_id: int | None = None
    object_id: int | None = None
    mission_id: int | None = None

    inspection_pan: int | None = None
    inspection_tilt: int | None = None
    inspection_lift: int | None = None


def decode_xml(raw: bytes) -> str:
    """Decode XML bytes, preferring UTF-8 with EUC-KR fallback.

    Vendor docs imply UTF-8 but historical Korean industrial systems often
    ship EUC-KR.  We try UTF-8 strictly first; on failure we retry as
    EUC-KR.  If both fail we raise rather than silently corrupting data.

    Exposed publicly so the ingestion handler can call it once and store
    the decoded text into ``dust_inspection.raw_xml`` without paying for
    a second decode inside :func:`parse_dust_inspection`.
    """
    try:
        return raw.decode(XML_PRIMARY_ENCODING)
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode(XML_FALLBACK_ENCODING)
    except UnicodeDecodeError as exc:
        raise XmlDecodeError(
            f"XML body decodes as neither {XML_PRIMARY_ENCODING} nor "
            f"{XML_FALLBACK_ENCODING}"
        ) from exc


def _coerce(
    raw: str | None, caster: Callable[[str], _T], tag: str
) -> _T | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return caster(text)
    except (ValueError, TypeError) as exc:
        raise XmlParseError(f"Cannot parse <{tag}> value {text!r}: {exc}") from exc


def parse_dust_inspection(raw: bytes) -> DustInspectionPayload:
    """Parse a ``<ELEMENT>`` body into a :class:`DustInspectionPayload`.

    Raises:
        XmlDecodeError: byte decoding failed entirely.
        XmlParseError:  XML malformed, required tag missing, or numeric
                        field unparseable.
    """
    text = decode_xml(raw)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise XmlParseError(f"Malformed XML: {exc}") from exc

    if root.tag != "ELEMENT":
        raise XmlParseError(
            f"Expected root <ELEMENT>, got <{root.tag}>"
        )

    def get_text(tag: str) -> str | None:
        node = root.find(tag)
        return node.text if node is not None else None

    def get_text_either(*tags: str) -> str | None:
        """Return the first non-empty match.  Used for fields that the vendor
        has shipped under more than one name (typo correction)."""
        for t in tags:
            val = get_text(t)
            if val is not None and val.strip():
                return val
        return None

    cmd_id_raw = get_text("CMD_ID")
    if cmd_id_raw is None or not cmd_id_raw.strip():
        raise XmlParseError("Missing required <CMD_ID>")
    cmd_id = cmd_id_raw.strip()
    if cmd_id != CMD_ID_DUST_INSPECTION:
        raise XmlParseError(
            f"Unsupported CMD_ID {cmd_id!r}; expected "
            f"{CMD_ID_DUST_INSPECTION!r}"
        )

    return DustInspectionPayload(
        cmd_id=cmd_id,
        dust_data=_coerce(get_text("DUST_DATA"), float, "DUST_DATA"),
        # Spec revision: <DUST_ALARM> (corrected) ↔ <DUST_ALRAM> (legacy typo)
        dust_alarm=_coerce(
            get_text_either("DUST_ALARM", "DUST_ALRAM"), int, "DUST_ALARM"
        ),
        datetime_str=(get_text("DATETIME") or "").strip() or None,
        sensor_type=_coerce(get_text("SENSOR_TYPE"), int, "SENSOR_TYPE"),
        sensor_index=_coerce(get_text("SENSOR_INDEX"), int, "SENSOR_INDEX"),
        target_index=_coerce(get_text("TARGET_INDEX"), int, "TARGET_INDEX"),
        waypoint_x=_coerce(get_text("WAYPOINT_X"), float, "WAYPOINT_X"),
        waypoint_y=_coerce(get_text("WAYPOINT_Y"), float, "WAYPOINT_Y"),
        waypoint_z=_coerce(get_text("WAYPOINT_Z"), float, "WAYPOINT_Z"),
        rot_x=_coerce(get_text("ROT_X"), float, "ROT_X"),
        rot_y=_coerce(get_text("ROT_Y"), float, "ROT_Y"),
        rot_z=_coerce(get_text("ROT_Z"), float, "ROT_Z"),
        rot_w=_coerce(get_text("ROT_W"), float, "ROT_W"),
        ugv_id=_coerce(get_text("UGV_ID"), int, "UGV_ID"),
        location_id=_coerce(get_text("LOCATION_ID"), int, "LOCATION_ID"),
        map_id=_coerce(get_text("MAP_ID"), int, "MAP_ID"),
        navigation_id=_coerce(get_text("NAVIGATION_ID"), int, "NAVIGATION_ID"),
        exec_id=_coerce(get_text("EXEC_ID"), int, "EXEC_ID"),
        plant_id=_coerce(get_text("PLANT_ID"), int, "PLANT_ID"),
        target_id=_coerce(get_text("TARGET_ID"), int, "TARGET_ID"),
        waypoint_id=_coerce(get_text("WAYPOINT_ID"), int, "WAYPOINT_ID"),
        # Spec revision: <INSPECTION_LOCAL_ID> (corrected) ↔ <INSPECTION_LOACL_ID> (legacy typo)
        inspection_local_id=_coerce(
            get_text_either("INSPECTION_LOCAL_ID", "INSPECTION_LOACL_ID"),
            int,
            "INSPECTION_LOCAL_ID",
        ),
        object_id=_coerce(get_text("OBJECT_ID"), int, "OBJECT_ID"),
        mission_id=_coerce(get_text("MISSION_ID"), int, "MISSION_ID"),
        inspection_pan=_coerce(get_text("INSPECTION_PAN"), int, "INSPECTION_PAN"),
        inspection_tilt=_coerce(get_text("INSPECTION_TILT"), int, "INSPECTION_TILT"),
        inspection_lift=_coerce(get_text("INSPECTION_LIFT"), int, "INSPECTION_LIFT"),
    )
