"""sd-cleaner entry point.

Two modes:
  * ``python -m cleaner.main``         — service mode: sleep until next
    schedule (default 03:00 KST), run cleanup, repeat. Also LISTENs on the
    PostgreSQL ``cleanup_trigger`` channel — a ``NOTIFY cleanup_trigger``
    (sent by the admin UI's [지금 정리] button) wakes the loop and runs
    cleanup immediately, then returns to the regular schedule.
  * ``python -m cleaner.main --once``  — one-shot: run cleanup
    immediately and exit (useful for manual triggering and dev tests).
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncpg
import structlog

from . import cleanup
from .config import CleanerSettings
from .logging_config import configure_logging

KST = ZoneInfo("Asia/Seoul")
TRIGGER_CHANNEL = "cleanup_trigger"
log = structlog.get_logger("cleaner.main")


def _next_scheduled_run(now_utc: datetime, hour_kst: int, minute_kst: int) -> datetime:
    """Return the next UTC-aware datetime where local clock = HH:MM KST.
    If we are exactly at or past that moment today (KST), schedule tomorrow."""
    now_kst = now_utc.astimezone(KST)
    candidate = now_kst.replace(
        hour=hour_kst, minute=minute_kst, second=0, microsecond=0,
    )
    if candidate <= now_kst:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    def _request_stop() -> None:
        log.info("shutdown_requested")
        stop.set()
    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                pass


async def _run_once(settings: CleanerSettings) -> None:
    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    assert pool is not None
    try:
        await cleanup.run_all(pool, settings)
        # --once 도 수동 실행이므로 '지금 정리' 버튼과 동일하게 프레임 전체 삭제.
        await cleanup.purge_all_frames(pool, settings)
    finally:
        await pool.close()


async def _listen_for_triggers(
    settings: CleanerSettings,
    trigger: asyncio.Event,
    stop: asyncio.Event,
) -> None:
    """Maintain a dedicated asyncpg connection that LISTENs on
    ``cleanup_trigger``. When NOTIFY arrives, set ``trigger`` so the main
    loop wakes up and runs cleanup immediately. Reconnects on failure so a
    transient DB blip does not silently kill manual triggers."""
    while not stop.is_set():
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(dsn=settings.dsn)

            def _on_notify(_conn, _pid, _channel, payload) -> None:  # noqa: ANN001
                log.info("manual_trigger_received", payload=payload or "")
                trigger.set()

            await conn.add_listener(TRIGGER_CHANNEL, _on_notify)
            log.info("listener_connected", channel=TRIGGER_CHANNEL)
            await stop.wait()
            return
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("listener_failed_reconnecting")
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass


async def _run_service(settings: CleanerSettings) -> None:
    pool = await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    assert pool is not None
    stop = asyncio.Event()
    trigger = asyncio.Event()
    _install_signal_handlers(stop)

    listener_task = asyncio.create_task(
        _listen_for_triggers(settings, trigger, stop),
        name="cleanup_listener",
    )

    try:
        while not stop.is_set():
            now = datetime.now(timezone.utc)
            next_run = _next_scheduled_run(
                now, settings.run_at_hour_kst, settings.run_at_minute_kst,
            )
            seconds_until_next = max(1.0, (next_run - now).total_seconds())
            # 디스크 체크 주기와 스케줄까지 남은 시간 중 짧은 쪽으로 깨어남.
            # 깨어날 때마다 emergency_purge로 디스크 압박을 검사하고,
            # 스케줄이 도래했거나 수동 트리거면 추가로 run_all도 돌린다.
            delay = min(
                float(settings.disk_check_interval_sec),
                seconds_until_next,
            )
            log.info(
                "sleeping_until_next_wake",
                next_run_utc=next_run.isoformat(),
                delay_sec=round(delay, 0),
                disk_check_interval_sec=settings.disk_check_interval_sec,
                run_at_kst=f"{settings.run_at_hour_kst:02d}:{settings.run_at_minute_kst:02d}",
            )
            stop_task = asyncio.create_task(stop.wait())
            trig_task = asyncio.create_task(trigger.wait())
            try:
                _, pending = await asyncio.wait(
                    {stop_task, trig_task},
                    timeout=delay,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for t in (stop_task, trig_task):
                    if not t.done():
                        t.cancel()

            if stop.is_set():
                break

            # 깨어남 원인을 분류한다 (셋 다 동시 가능).
            schedule_due = datetime.now(timezone.utc) >= next_run
            manual_triggered = trigger.is_set()
            trigger.clear()

            # 수동 '지금 정리' 버튼 = 임계치/나이 무시하고 cctv_frame 전체 즉시
            # 삭제(파일+DB행).  자동 주기 깨어남 = 디스크 압박 안전장치(85% 게이트).
            if manual_triggered:
                try:
                    await cleanup.purge_all_frames(pool, settings)
                except Exception:
                    log.exception("manual_frame_purge_failed")
            else:
                try:
                    await cleanup.emergency_purge(pool, settings)
                except Exception:
                    log.exception("emergency_purge_failed")

            # 일반 retention sweep은 스케줄 도래 또는 수동 트리거일 때만.
            if schedule_due or manual_triggered:
                tick_reason = "manual" if manual_triggered else "schedule"
                log.info("tick_start", trigger=tick_reason)
                try:
                    await cleanup.run_all(pool, settings)
                except Exception:
                    log.exception("tick_failed")
                log.info("tick_done", trigger=tick_reason)
    finally:
        listener_task.cancel()
        try:
            await listener_task
        except (asyncio.CancelledError, Exception):
            pass
        await pool.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="cleaner")
    p.add_argument(
        "--once", action="store_true",
        help="run cleanup once and exit (skip the schedule loop)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    settings = CleanerSettings()
    configure_logging(settings.log_level, settings.log_format)
    log.info(
        "cleaner_starting",
        mode="once" if args.once else "service",
        db_host=settings.db_host,
        storage_root=settings.storage_root,
        retention_days={
            "video_normal": settings.video_normal_days,
            "video_anomaly": settings.video_anomaly_days,
            "sensor": settings.sensor_days,
            "ingestion_log": settings.ingestion_log_days,
        },
        emergency={
            "purge_at_percent": settings.emergency_purge_at_percent,
            "target_percent": settings.emergency_target_percent,
            "disk_check_interval_sec": settings.disk_check_interval_sec,
        },
    )
    try:
        if args.once:
            asyncio.run(_run_once(settings))
        else:
            asyncio.run(_run_service(settings))
    except KeyboardInterrupt:
        pass
    log.info("cleaner_stopped")


if __name__ == "__main__":
    main()
