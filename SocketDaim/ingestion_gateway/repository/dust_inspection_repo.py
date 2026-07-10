"""dust_inspection repository.

INSERT only.  The Correlator (separate module) is the only consumer that
ever UPDATEs this table's children — this repo does not need an update
method.
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from gw_proto.codec.loas.dust_framing import DustHeader
from gw_proto.codec.loas.dust_xml import DustInspectionPayload

_INSERT_SQL = """
    INSERT INTO dust_inspection (
        received_at, sensor_datetime, sensor_epoch_sec,
        cmd_id, data_object_id, protocol_version,
        dust_value, dust_alarm, sensor_type, sensor_index, target_index,
        waypoint_x, waypoint_y, waypoint_z,
        rot_x, rot_y, rot_z, rot_w,
        ugv_id, location_id, map_id, navigation_id,
        exec_id, plant_id, target_id, waypoint_id,
        inspection_local_id, object_id, mission_id,
        inspection_pan, inspection_tilt, inspection_lift,
        raw_xml
    ) VALUES (
        COALESCE($1, clock_timestamp()),
        $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
        $12, $13, $14, $15, $16, $17, $18,
        $19, $20, $21, $22, $23, $24, $25, $26,
        $27, $28, $29, $30, $31, $32, $33
    )
    RETURNING id
"""


def _parse_sensor_datetime(raw: str | None) -> datetime | None:
    """Parse the XML ``<DATETIME>`` field into UTC-tagged datetime.

    Vendor format observed in spec: ``YYYY-MM-DD HH:MM:SS.ffffff`` with no
    timezone marker.  We attach UTC to match the 12-byte header's
    ``timestamp`` semantics; if the vendor turns out to ship local time
    the only consumer is the audit ``sensor_datetime`` column, so the
    correction is a single column-update migration away.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    # Common variants we accept without complaint.
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(text, fmt)
            return naive.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # ISO 8601 form, just in case.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class DustInspectionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(
        self,
        hdr: DustHeader,
        payload: DustInspectionPayload,
        *,
        raw_xml: str,
        received_at: datetime | None = None,
    ) -> int:
        """Insert one row; return its ``id``.

        ``received_at`` defaults to ``clock_timestamp()`` at the database —
        only override for back-dating or in tests.
        """
        return await self._pool.fetchval(
            _INSERT_SQL,
            received_at,
            _parse_sensor_datetime(payload.datetime_str),
            hdr.timestamp,
            payload.cmd_id,
            hdr.data_object_id,
            hdr.version,
            payload.dust_data,
            payload.dust_alarm,
            payload.sensor_type,
            payload.sensor_index,
            payload.target_index,
            payload.waypoint_x,
            payload.waypoint_y,
            payload.waypoint_z,
            payload.rot_x,
            payload.rot_y,
            payload.rot_z,
            payload.rot_w,
            payload.ugv_id,
            payload.location_id,
            payload.map_id,
            payload.navigation_id,
            payload.exec_id,
            payload.plant_id,
            payload.target_id,
            payload.waypoint_id,
            payload.inspection_local_id,
            payload.object_id,
            payload.mission_id,
            payload.inspection_pan,
            payload.inspection_tilt,
            payload.inspection_lift,
            raw_xml,
        )
