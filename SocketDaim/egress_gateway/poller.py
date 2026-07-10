"""Periodic polling loop — fetch pending decisions and hand to Sender."""

from __future__ import annotations

import asyncio

import structlog

from .repository import DecisionRepository
from .sender import Sender

logger = structlog.get_logger(__name__)


class Poller:
    def __init__(
        self,
        repo: DecisionRepository,
        sender: Sender,
        *,
        interval_sec: float,
        batch_size: int,
    ) -> None:
        self._repo = repo
        self._sender = sender
        self._interval = interval_sec
        self._batch = batch_size

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info(
            "poller_started",
            interval_sec=self._interval,
            batch_size=self._batch,
        )
        while not stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("poller_tick_error")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass  # interval elapsed, continue
        logger.info("poller_stopped")

    async def _tick(self) -> None:
        records = await self._repo.fetch_pending(self._batch)
        if not records:
            return
        logger.debug("poller_tick", pending=len(records))
        for rec in records:
            ok = await self._sender.send_record(rec)
            if not ok:
                # send failed — break to retry on next tick (avoid hammering down peer)
                break
