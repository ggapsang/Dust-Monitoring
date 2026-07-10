from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from dumopro_core.keys import channel_candle
from dumopro_core.redis_client import RedisClient

log = logging.getLogger(__name__)


class Broadcaster:
    """Redis pub/sub → per-connection asyncio.Queue fan-out.

    Maintains exactly one Redis subscription per station regardless of how many
    SSE clients are connected. Dropped queues are cleaned up when clients
    disconnect.
    """

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis
        self._queues: dict[str, set[asyncio.Queue[str]]] = {}
        self._pumps: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        # Multiplexed (all-station) subscription: one PSUBSCRIBE pump fans out
        # every station's events to a single set of queues.  Lets the frontend
        # use ONE EventSource for all stations instead of one-per-station
        # (avoids HTTP/1.1 6-connection-per-origin exhaustion).
        self._all_queues: set[asyncio.Queue[str]] = set()
        self._all_pump: asyncio.Task | None = None

    async def subscribe(self, station: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._queues.setdefault(station, set()).add(queue)
            if station not in self._pumps:
                self._pumps[station] = asyncio.create_task(
                    self._pump(station), name=f"sse-pump:{station}"
                )
        return queue

    async def unsubscribe(self, station: str, queue: asyncio.Queue[str]) -> None:
        async with self._lock:
            subs = self._queues.get(station)
            if subs:
                subs.discard(queue)
                if not subs:
                    self._queues.pop(station, None)
                    task = self._pumps.pop(station, None)
                    if task:
                        task.cancel()

    # ---- multiplexed (all-station) subscription -------------------------
    async def subscribe_all(self) -> asyncio.Queue[str]:
        """Subscribe to *every* station's events on one queue.  Each payload
        is augmented with a ``station`` field so the client can demux."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=512)
        async with self._lock:
            self._all_queues.add(queue)
            if self._all_pump is None:
                self._all_pump = asyncio.create_task(
                    self._pump_all(), name="sse-pump:all"
                )
        return queue

    async def unsubscribe_all(self, queue: asyncio.Queue[str]) -> None:
        async with self._lock:
            self._all_queues.discard(queue)
            if not self._all_queues and self._all_pump is not None:
                self._all_pump.cancel()
                self._all_pump = None

    async def _pump_all(self) -> None:
        prefix = channel_candle("")        # "channel:candle:"
        pattern = channel_candle("*")      # "channel:candle:*"
        pubsub = None
        try:
            pubsub = self._redis.raw.pubsub(ignore_subscribe_messages=True)
            await pubsub.psubscribe(pattern)
            log.info("sse.pump_all_start")
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    continue
                if message.get("type") != "pmessage":
                    continue
                channel = message.get("channel")
                if isinstance(channel, (bytes, bytearray)):
                    channel = channel.decode()
                data = message.get("data")
                if isinstance(data, (bytes, bytearray)):
                    data = data.decode()
                if not isinstance(channel, str) or not isinstance(data, str):
                    continue
                station = channel[len(prefix):]
                await self._fan_out_all(_inject_station(data, station))
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("sse.pump_all_error")
        finally:
            if pubsub is not None:
                try:
                    await pubsub.punsubscribe(pattern)
                    await pubsub.close()
                except Exception:
                    pass
            log.info("sse.pump_all_stop")

    async def _fan_out_all(self, payload: str) -> None:
        async with self._lock:
            subs = list(self._all_queues)
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("sse.all_queue_full (dropping slow subscriber)")
                try:
                    _ = q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass

    async def close(self) -> None:
        async with self._lock:
            tasks = list(self._pumps.values())
            if self._all_pump is not None:
                tasks.append(self._all_pump)
                self._all_pump = None
            self._pumps.clear()
            self._queues.clear()
            self._all_queues.clear()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    async def _pump(self, station: str) -> None:
        pubsub = None
        try:
            pubsub = self._redis.raw.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(channel_candle(station))
            log.info("sse.pump_start station=%s", station)
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, (bytes, bytearray)):
                    data = data.decode()
                if not isinstance(data, str):
                    continue
                await self._fan_out(station, data)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("sse.pump_error station=%s", station)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(channel_candle(station))
                    await pubsub.close()
                except Exception:
                    pass
            log.info("sse.pump_stop station=%s", station)

    async def _fan_out(self, station: str, payload: str) -> None:
        async with self._lock:
            subs = list(self._queues.get(station, ()))
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("sse.queue_full station=%s (dropping slow subscriber)", station)
                try:
                    _ = q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass


def _inject_station(data: str, station: str) -> str:
    """Add a ``station`` field to a candle JSON payload so a single
    multiplexed SSE stream can be demultiplexed client-side.  Non-JSON
    payloads are returned unchanged."""
    import json

    try:
        obj = json.loads(data)
        if isinstance(obj, dict):
            obj["station"] = station
            return json.dumps(obj, separators=(",", ":"))
    except Exception:
        pass
    return data


async def iter_sse(queue: asyncio.Queue[str], heartbeat_sec: float = 15.0) -> AsyncIterator[str]:
    """Yield SSE-formatted strings from a subscription queue.

    Heartbeat every `heartbeat_sec` seconds to keep proxies alive.
    """
    while True:
        try:
            payload = await asyncio.wait_for(queue.get(), timeout=heartbeat_sec)
        except asyncio.TimeoutError:
            yield ": ping\n\n"
            continue

        event_type = "message"
        try:
            import json

            obj = json.loads(payload)
            event_type = obj.get("type", "message")
        except Exception:
            pass
        yield f"event: {event_type}\ndata: {payload}\n\n"
