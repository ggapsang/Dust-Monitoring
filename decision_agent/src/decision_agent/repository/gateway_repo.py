"""Read-only gateway_db (SocketDaim) repository — station label lookup.

Resolves the human station label (별명) for the admin UI's ``station`` column.
Mapping path (SocketDaim, 2026-07 target_id-based station identity):

    decision_record.dust_id
      → gateway_db.dust_inspection.target_id
      → loas_station_id(target_id) = waypoint_label PK
      → waypoint_label.label (별명)  또는  'TGT-{target_id}' fallback

This repo only produces the label-or-'TGT-{target_id}' portion.  When a dust_id
has no row or its target_id is NULL, it is omitted from the result and the
caller displays 'TGT-?'.
"""

from __future__ import annotations

import asyncpg


_STATION_LABEL_SQL = """
    SELECT di.id AS dust_id, di.target_id, wl.label
      FROM dust_inspection di
      LEFT JOIN waypoint_label wl
        ON wl.station_id = loas_station_id(di.target_id)
     WHERE di.id = ANY($1::bigint[])
"""


class GatewayRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def station_labels(self, dust_ids: list[int]) -> dict[int, str]:
        """Return ``{dust_id: display_name}`` for resolvable dust_ids.

        ``display_name`` = the assigned label (별명) if present, else
        ``'TGT-{target_id}'``.  dust_ids with no dust_inspection row or a NULL
        target_id are omitted (caller falls back to 'TGT-?').
        """
        if not dust_ids:
            return {}
        rows = await self._pool.fetch(_STATION_LABEL_SQL, dust_ids)
        out: dict[int, str] = {}
        for r in rows:
            label = r["label"]
            target_id = r["target_id"]
            if label:
                out[r["dust_id"]] = label
            elif target_id is not None:
                out[r["dust_id"]] = f"TGT-{target_id}"
            # else: unresolvable → omit; caller uses 'TGT-?'
        return out
