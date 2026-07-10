"""Unit tests for ingestion_gateway.correlator.FrameCorrelator.

Pool is mocked; these tests verify only the loop wiring and parameter
binding.  Real pairing semantics live in
``test_correlator_integration.py`` against a postgres container.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from ingestion_gateway.correlator import FrameCorrelator, _parse_update_count


class TestParseUpdateCount:
    def test_standard_tag(self):
        assert _parse_update_count("UPDATE 17") == 17

    def test_zero(self):
        assert _parse_update_count("UPDATE 0") == 0

    def test_garbage_safe(self):
        assert _parse_update_count("") == 0
        assert _parse_update_count("WAT") == 0


class TestConstructorValidation:
    def test_invalid_interval(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="interval"):
            FrameCorrelator(pool, interval_sec=0)
        with pytest.raises(ValueError, match="interval"):
            FrameCorrelator(pool, interval_sec=-1)

    def test_invalid_window(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="window"):
            FrameCorrelator(pool, before_sec=-1)
        with pytest.raises(ValueError, match="window"):
            FrameCorrelator(pool, after_sec=-1)

    def test_invalid_lookback(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="lookback"):
            FrameCorrelator(pool, lookback_sec=0)

    def test_zero_window_is_allowed(self):
        """Exact-timestamp-only pairing is a valid (if strict) policy."""
        pool = AsyncMock()
        FrameCorrelator(pool, before_sec=0, after_sec=0)  # no raise


class TestTick:
    @pytest.mark.asyncio
    async def test_tick_passes_three_params_in_order(self):
        pool = AsyncMock()
        pool.execute.return_value = "UPDATE 0"
        c = FrameCorrelator(
            pool, before_sec=1.5, after_sec=2.5, lookback_sec=900.0
        )
        await c.tick()
        args = pool.execute.call_args.args
        # args[0] is SQL, args[1..3] are bind params
        assert args[1] == 1.5
        assert args[2] == 2.5
        assert args[3] == 900.0

    @pytest.mark.asyncio
    async def test_tick_returns_parsed_count(self):
        pool = AsyncMock()
        pool.execute.return_value = "UPDATE 42"
        c = FrameCorrelator(pool)
        assert await c.tick() == 42


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_run_invokes_tick_repeatedly(self):
        pool = AsyncMock()
        pool.execute.return_value = "UPDATE 1"
        c = FrameCorrelator(pool, interval_sec=0.05)

        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.18)  # ~3-4 ticks
        c.stop()
        await asyncio.wait_for(task, timeout=1.0)

        assert pool.execute.call_count >= 3

    @pytest.mark.asyncio
    async def test_run_survives_tick_exception(self):
        pool = AsyncMock()
        call = {"n": 0}

        async def flaky(*_args, **_kw):
            call["n"] += 1
            if call["n"] == 1:
                raise RuntimeError("simulated transient failure")
            return "UPDATE 1"

        pool.execute = flaky
        c = FrameCorrelator(pool, interval_sec=0.05)

        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.18)
        c.stop()
        await asyncio.wait_for(task, timeout=1.0)

        # First tick raised; second + third succeeded
        assert call["n"] >= 2

    @pytest.mark.asyncio
    async def test_stop_terminates_loop_promptly(self):
        pool = AsyncMock()
        pool.execute.return_value = "UPDATE 0"
        c = FrameCorrelator(pool, interval_sec=60.0)  # long interval

        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.05)  # let first tick start
        c.stop()
        # Should return well within the 60-second interval
        await asyncio.wait_for(task, timeout=1.0)
