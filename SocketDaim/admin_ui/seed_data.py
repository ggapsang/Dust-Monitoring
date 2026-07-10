"""Single source of truth for the 4 sample stations.

Replaces the old `seed_test_stations.sql` boot-time mount.  The Admin UI's
"Seed 4 samples" button INSERTs these on demand, idempotently.
"""

from __future__ import annotations

from typing import NamedTuple


class SampleStation(NamedTuple):
    station_name: str
    location_info: str
    capture_cycle: int
    status: str


SAMPLE_STATIONS: tuple[SampleStation, ...] = (
    SampleStation("FL-A01-NORTH", "Fab A line 1, north sector", 60, "collecting"),
    SampleStation("FL-A02-SOUTH", "Fab A line 2, south sector", 60, "collecting"),
    SampleStation("FL-B01-EAST",  "Fab B line 1, east sector",  60, "collecting"),
    SampleStation("FL-C01-WEST",  "Fab C line 1, west sector",  60, "collecting"),
)
