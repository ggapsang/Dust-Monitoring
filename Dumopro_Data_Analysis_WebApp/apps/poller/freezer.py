from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from dumopro_core.keys import Unit

log = logging.getLogger(__name__)

UNITS: tuple[Unit, ...] = ("day", "week", "month")


@dataclass
class PendingFreeze:
    bucket_key: str
    ready_at: float  # wall-clock epoch seconds


@dataclass
class GraceFreezer:
    """Queues bucket freezes behind a wall-clock grace period.

    Wall-clock is used (not virtual-time from samples): under Mock's virtual
    time (real 10min = virtual 1day), day boundaries cross every ~10 real
    minutes, so a 30s wall-clock grace causes near-immediate freezing once a
    new bucket is observed. The plan (§10.2) treats this as normal.
    """

    grace_seconds: float
    pending: dict[Unit, PendingFreeze] = field(default_factory=dict)

    def schedule(self, unit: Unit, bucket_key: str) -> None:
        if unit in self.pending and self.pending[unit].bucket_key == bucket_key:
            return
        self.pending[unit] = PendingFreeze(bucket_key, time.time() + self.grace_seconds)

    def due(self) -> list[tuple[Unit, str]]:
        now = time.time()
        due_list: list[tuple[Unit, str]] = []
        for unit in UNITS:
            pf = self.pending.get(unit)
            if pf and pf.ready_at <= now:
                due_list.append((unit, pf.bucket_key))
        return due_list

    def drop(self, unit: Unit, bucket_key: str) -> None:
        pf = self.pending.get(unit)
        if pf and pf.bucket_key == bucket_key:
            del self.pending[unit]

    def force_freeze_all(self) -> list[tuple[Unit, str]]:
        """Flush all pending entries regardless of wall-clock deadline."""
        out = [(u, pf.bucket_key) for u, pf in self.pending.items()]
        self.pending.clear()
        return out
