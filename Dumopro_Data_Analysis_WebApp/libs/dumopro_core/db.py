from __future__ import annotations

import asyncpg

from .models import SampleRow, StationInfo


# ---------------------------------------------------------------------------
# Source-table allowlist
# ---------------------------------------------------------------------------
# SQL identifier values come from environment variables (STATION_SOURCE /
# SAMPLE_SOURCE) so they MUST be checked against a fixed allowlist before
# being interpolated into the query string — otherwise we'd open the door to
# trivial injection.

_ALLOWED_STATION_SOURCES = frozenset({"station", "v_loas_stations"})
_ALLOWED_SAMPLE_SOURCES = frozenset({"sensor_sample", "v_loas_sensor_sample"})


def _check(name: str, allowed: frozenset[str], label: str) -> str:
    if name not in allowed:
        raise ValueError(
            f"Disallowed {label} source: {name!r} (allowed: {sorted(allowed)})"
        )
    return name


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

async def init_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------

async def fetch_stations(
    pool: asyncpg.Pool,
    *,
    source: str = "v_loas_stations",
) -> list[StationInfo]:
    src = _check(source, _ALLOWED_STATION_SOURCES, "station")
    rows = await pool.fetch(
        f"""
        SELECT station_id::text AS station_id,
               station_name,
               status,
               location_info
        FROM {src}
        ORDER BY station_name
        """
    )
    return [StationInfo(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------

async def fetch_samples_since(
    pool: asyncpg.Pool,
    station_id: str,
    last_id: int,
    measurement_type: str,
    limit: int,
    *,
    source: str = "v_loas_sensor_sample",
) -> list[SampleRow]:
    src = _check(source, _ALLOWED_SAMPLE_SOURCES, "sample")
    rows = await pool.fetch(
        f"""
        SELECT id, station_id::text AS station_id, measurement_type,
               value, unit, sampled_at
        FROM {src}
        WHERE station_id = $1::uuid
          AND measurement_type = $2
          AND id > $3
        ORDER BY id
        LIMIT $4
        """,
        station_id,
        measurement_type,
        last_id,
        limit,
    )
    return [SampleRow(**dict(r)) for r in rows]


async def fetch_samples_latest(
    pool: asyncpg.Pool,
    station_id: str,
    measurement_type: str,
    limit: int,
    *,
    source: str = "v_loas_sensor_sample",
) -> list[SampleRow]:
    """Latest N samples in chronological order (oldest first of the N newest)."""
    src = _check(source, _ALLOWED_SAMPLE_SOURCES, "sample")
    rows = await pool.fetch(
        f"""
        SELECT id, station_id::text AS station_id, measurement_type,
               value, unit, sampled_at
        FROM {src}
        WHERE station_id = $1::uuid
          AND measurement_type = $2
        ORDER BY id DESC
        LIMIT $3
        """,
        station_id,
        measurement_type,
        limit,
    )
    # Reverse in Python so caller gets oldest→newest.
    return [SampleRow(**dict(r)) for r in reversed(rows)]


async def iter_all_samples(
    pool: asyncpg.Pool,
    station_id: str,
    measurement_type: str,
    *,
    chunk: int = 5000,
    source: str = "v_loas_sensor_sample",
):
    """Yield samples in id order. Used for cold-start backfill."""
    src = _check(source, _ALLOWED_SAMPLE_SOURCES, "sample")
    last_id = 0
    while True:
        rows = await pool.fetch(
            f"""
            SELECT id, station_id::text AS station_id, measurement_type,
                   value, unit, sampled_at
            FROM {src}
            WHERE station_id = $1::uuid
              AND measurement_type = $2
              AND id > $3
            ORDER BY id
            LIMIT $4
            """,
            station_id,
            measurement_type,
            last_id,
            chunk,
        )
        if not rows:
            return
        for r in rows:
            yield SampleRow(**dict(r))
        last_id = rows[-1]["id"]
        if len(rows) < chunk:
            return
