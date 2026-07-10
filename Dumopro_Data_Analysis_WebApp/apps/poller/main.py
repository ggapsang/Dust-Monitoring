from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from dumopro_core.config import Settings, get_settings
from dumopro_core.db import fetch_stations, init_pool
from dumopro_core.models import StationInfo
from dumopro_core.redis_client import RedisClient

from .health import HealthState, serve as serve_health
from .station_task import StationTask

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("poller.main")


@dataclass
class _Running:
    """Tracks one running StationTask + its asyncio.Task handle."""
    info: StationInfo
    instance: StationTask
    task: asyncio.Task


# Shared state owned by the top-level run() coroutine.
# Keyed by station_name.
StationTaskMap = dict[str, _Running]


async def _sync_stations_list(redis: RedisClient, stations: list[StationInfo]) -> None:
    payload = [s.model_dump() for s in stations]
    await redis.set_stations(payload)
    log.info("stations.synced count=%d", len(payload))


async def _start_task(
    *,
    station: StationInfo,
    pool,
    redis: RedisClient,
    settings: Settings,
    running: StationTaskMap,
    cold_signal: asyncio.Event | None = None,
) -> None:
    """Create and schedule a StationTask, register it in `running`."""
    instance = StationTask(station, pool, redis, settings, cold_start_signal=cold_signal)
    task = asyncio.create_task(
        instance.run(), name=f"station:{station.station_name}",
    )
    running[station.station_name] = _Running(
        info=station, instance=instance, task=task,
    )
    # We're starting fresh for this name; clear any "removed" marker.
    try:
        await redis.clear_removed_station_id(station.station_name)
    except Exception:
        log.exception("task.start_clear_removed_failed station=%s", station.station_name)
    log.info("task.started station=%s id=%s", station.station_name, station.station_id)


async def _stop_task(
    running: StationTaskMap, station_name: str, redis: RedisClient
) -> None:
    """Stop a StationTask gracefully and remove it from `running`.
    Does NOT delete any Redis data.  Records the stopped UUID into
    `stations:removed` so a future re-registration with a different
    UUID can be flagged as a conflict."""
    rec = running.pop(station_name, None)
    if rec is None:
        return
    try:
        await redis.set_removed_station_id(
            station_name, str(rec.info.station_id),
        )
    except Exception:
        log.exception("task.stop_remember_failed station=%s", station_name)

    rec.instance.stop()
    try:
        await asyncio.wait_for(rec.task, timeout=5.0)
    except asyncio.TimeoutError:
        log.warning("task.stop_timeout station=%s; cancelling", station_name)
        rec.task.cancel()
        try:
            await rec.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    except Exception:
        log.exception("task.stop_error station=%s", station_name)
    log.info("task.stopped station=%s", station_name)


async def _reconcile_once(
    *,
    pool,
    redis: RedisClient,
    settings: Settings,
    running: StationTaskMap,
) -> None:
    """Compare DB → Redis → running tasks; start/stop/conflict as needed."""
    try:
        curr_list = await fetch_stations(pool, source=settings.station_source)
    except Exception:
        log.exception("reconcile.fetch_failed")
        return

    curr: dict[str, StationInfo] = {s.station_name: s for s in curr_list}
    prev: dict[str, dict] = {s["station_name"]: s for s in await redis.get_stations()}

    # Persist the latest DB view in Redis so the API/UI sees added/removed
    # stations even if a task could not start (e.g. pending conflict).
    await _sync_stations_list(redis, curr_list)

    names = set(prev) | set(curr) | set(running)

    for name in sorted(names):
        prev_st = prev.get(name)
        curr_st = curr.get(name)

        # 1) Removed from DB → stop task, preserve Redis data.
        if prev_st is not None and curr_st is None:
            if name in running:
                await _stop_task(running, name, redis)
            continue

        # 2) Pending conflict (left over from a previous tick).  Make sure
        #    no task is running and skip until the user resolves it.
        if curr_st is not None and await redis.has_pending_conflict(name):
            if name in running:
                # Most likely the conflict was just detected this tick.
                await _stop_task(running, name, redis)
            continue

        # 3) Name appears in DB but no running task.  Distinguish:
        #    3a) re-registration via stop+restart cycle (we remembered the
        #        old UUID in stations:removed)
        #    3b) re-registration with no memory but residual Redis data
        #        (poller restart, lost memory) — old UUID unknown
        #    3c) genuinely new station
        if curr_st is not None and name not in running:
            new_id = str(curr_st.station_id)
            removed_id = await redis.get_removed_station_id(name)
            if removed_id and removed_id != new_id:
                log.warning(
                    "reconcile.reregistration_via_removal station=%s old=%s new=%s",
                    name, removed_id, new_id,
                )
                await redis.set_pending_conflict(
                    name, old_id=removed_id, new_id=new_id,
                )
                continue
            if not removed_id and await redis.has_station_remnant_data(name):
                log.warning(
                    "reconcile.remnant_data station=%s new=%s old=unknown",
                    name, new_id,
                )
                await redis.set_pending_conflict(
                    name, old_id="unknown", new_id=new_id,
                )
                continue
            # 3c: genuinely new
            await _start_task(
                station=curr_st, pool=pool, redis=redis, settings=settings,
                running=running,
            )
            continue

        # 4) Name already running — check station_id drift (in-memory case).
        if curr_st is not None and name in running:
            running_id = str(running[name].info.station_id)
            new_id = str(curr_st.station_id)
            if running_id == new_id:
                continue
            # Re-registration captured live: same name, different UUID
            # without an intermediate stop.
            log.warning(
                "reconcile.reregistration station=%s old=%s new=%s",
                name, running_id, new_id,
            )
            await _stop_task(running, name, redis)
            await redis.set_pending_conflict(
                name, old_id=running_id, new_id=new_id,
            )


async def _sync_loop(
    *,
    pool,
    redis: RedisClient,
    settings: Settings,
    running: StationTaskMap,
    lock: asyncio.Lock,
) -> None:
    """Periodic reconcile every settings.station_refresh_sec seconds."""
    interval = max(10.0, float(settings.station_refresh_sec))
    log.info("sync_loop.start interval=%.0fs", interval)
    while True:
        await asyncio.sleep(interval)
        async with lock:
            await _reconcile_once(
                pool=pool, redis=redis, settings=settings, running=running,
            )
            log.info("sync_loop.tick running=%d", len(running))


async def _sync_trigger_listener(
    *,
    pool,
    redis: RedisClient,
    settings: Settings,
    running: StationTaskMap,
    lock: asyncio.Lock,
) -> None:
    """Subscribe to channel:station-sync; reconcile on every message."""
    pubsub = await redis.subscribe_sync_trigger()
    log.info("sync_trigger.listening")
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            log.info("sync_trigger.received reconcile=now")
            async with lock:
                await _reconcile_once(
                    pool=pool, redis=redis, settings=settings, running=running,
                )
    finally:
        try:
            await pubsub.unsubscribe()
            await pubsub.close()
        except Exception:
            pass


async def run() -> None:
    settings = get_settings()
    log.info("poller.boot pg=%s redis=%s", settings.pg_dsn.split("@")[-1], settings.redis_url)

    pool = await init_pool(settings.pg_dsn)
    redis = RedisClient(settings.redis_url)
    await redis.ping()

    initial_stations = await fetch_stations(pool, source=settings.station_source)
    await _sync_stations_list(redis, initial_stations)

    health_state = HealthState()
    health_state.station_count = len(initial_stations)

    running: StationTaskMap = {}
    cold_signals: list[asyncio.Event] = []

    # Initial task fan-out.  Skip stations that already have a pending
    # conflict (left over from a previous run).
    for st in initial_stations:
        if await redis.has_pending_conflict(st.station_name):
            log.info("boot.skip_conflict station=%s", st.station_name)
            continue
        ev = asyncio.Event()
        cold_signals.append(ev)
        await _start_task(
            station=st, pool=pool, redis=redis, settings=settings,
            running=running, cold_signal=ev,
        )

    bg: list[asyncio.Task] = []
    bg.append(
        asyncio.create_task(serve_health(health_state, settings.health_port), name="health")
    )

    reconcile_lock = asyncio.Lock()
    bg.append(
        asyncio.create_task(
            _sync_loop(
                pool=pool, redis=redis, settings=settings,
                running=running, lock=reconcile_lock,
            ),
            name="sync_loop",
        )
    )
    bg.append(
        asyncio.create_task(
            _sync_trigger_listener(
                pool=pool, redis=redis, settings=settings,
                running=running, lock=reconcile_lock,
            ),
            name="sync_trigger",
        )
    )

    async def mark_ready() -> None:
        if cold_signals:
            await asyncio.gather(*(ev.wait() for ev in cold_signals))
        health_state.cold_start_done = True
        # one more tick interval before declaring warm
        await asyncio.sleep(settings.poll_interval_sec * 2)
        health_state.tick_seen = True
        log.info("poller.ready station_count=%d", len(running))

    bg.append(asyncio.create_task(mark_ready(), name="ready_marker"))

    try:
        # Wait forever — bg tasks loop indefinitely.
        await asyncio.gather(*bg)
    finally:
        log.info("poller.shutting_down running_tasks=%d", len(running))
        for name in list(running):
            await _stop_task(running, name, redis)
        await redis.close()
        await pool.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("poller.shutdown")


if __name__ == "__main__":
    main()
