"""Deferred batch pairing of cctv_frame rows to dust_inspection rows.

The DUST and CCTV listeners are mutually ignorant — DUST stores rich
metadata (mission/waypoint/UGV ids) keyed by ``received_at``, CCTV stores
images keyed by the same clock.  This module is the bridge: every N
seconds it scans recent unpaired frames, joins each one to the *nearest*
DUST inspection whose ``received_at`` is within a configurable window,
and writes the FK + ``paired_at`` into ``cctv_frame``.

Why deferred and not online (i.e. tag on DUST arrival):
* A dust event at time T should also pair with frames that arrive at
  T + W_after, which haven't happened yet at handler time.
* Decoupling keeps the listeners simple and self-contained — they only
  need INSERT.  The Correlator owns the only UPDATE path on cctv_frame.

Tie-breaking when one frame sits inside two dust events' windows:
``DISTINCT ON (frame.id) ... ORDER BY frame.id, abs(time diff)`` —
nearest-in-time wins.  This makes the pairing deterministic and gives
each frame at most one inspection_id.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)


_PAIR_SQL = """
UPDATE cctv_frame f
   SET dust_inspection_id = sub.inspection_id,
       paired_at          = clock_timestamp()
  FROM (
    SELECT DISTINCT ON (f2.id)
           f2.id  AS frame_id,
           di.id  AS inspection_id
      FROM cctv_frame f2
      JOIN dust_inspection di
        ON f2.received_at BETWEEN
                di.received_at - make_interval(secs => $1)
            AND di.received_at + make_interval(secs => $2)
     WHERE f2.dust_inspection_id IS NULL
       AND f2.received_at > clock_timestamp() - make_interval(secs => $3)
     ORDER BY f2.id,
              abs(extract(epoch FROM (f2.received_at - di.received_at)))
  ) sub
 WHERE f.id = sub.frame_id
"""


def _parse_update_count(execute_result: str) -> int:
    """Parse asyncpg ``execute()`` tag — e.g. ``'UPDATE 17'`` → 17."""
    parts = execute_result.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


class FrameCorrelator:
    """Periodic dust↔frame pairing task."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        interval_sec: float = 10.0,
        before_sec: float = 2.0,
        after_sec: float = 2.0,
        lookback_sec: float = 600.0,
    ) -> None:
        if interval_sec <= 0:
            raise ValueError("interval_sec must be positive")
        if before_sec < 0 or after_sec < 0:
            raise ValueError("window seconds must be non-negative")
        if lookback_sec <= 0:
            raise ValueError("lookback_sec must be positive")
        self._pool = pool
        self._interval = interval_sec
        self._before = before_sec
        self._after = after_sec
        self._lookback = lookback_sec
        self._stop = asyncio.Event()

    # -- public API --------------------------------------------------------

    async def tick(self) -> int:
        """Run one pairing pass; return the number of frames newly paired."""
        tag = await self._pool.execute(
            _PAIR_SQL, self._before, self._after, self._lookback
        )
        return _parse_update_count(tag)

    async def run(self) -> None:
        """Tick once immediately, then every ``interval_sec`` until
        :meth:`stop` is called.  Per-tick exceptions are logged and the
        loop continues — a transient DB failure must not silently halt
        pairing forever."""
        while not self._stop.is_set():
            try:
                count = await self.tick()
                if count:
                    logger.info("correlator_paired", extra={"count": count})
            except Exception:
                logger.exception("correlator_tick_failed")

            try:
                # asyncio.wait_for raises TimeoutError when the interval
                # elapses without stop() being called.  Set ⇒ exit loop.
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Signal :meth:`run` to exit at the next interval boundary."""
        self._stop.set()
