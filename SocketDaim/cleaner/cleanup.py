"""Retention enforcement transactions.

Policy (gateway_plan.md §9.3):
  - normal video  : is_valid AND NOT is_excluded  → 14 days
  - anomaly video : NOT is_valid OR is_excluded   → 180 days
  - sensor_sample                                 → 180 days
  - ingestion_log                                 → 180 days

Video purges run in batches (LIMIT N) so a backlog can't lock the table
for too long.  File unlink happens before DB DELETE — if unlink fails
the DELETE still proceeds and the warning is logged (orphan files are
preferable to orphan DB rows that block the FK).

Two modes:
  run_all()        — nightly retention sweep (age-based, per the policy above)
  emergency_purge()— pressure-based safety net. Triggers when storage_root
                     usage exceeds emergency_purge_at_percent and deletes
                     the oldest cctv_frame JPEGs (file on disk + DB row)
                     until usage falls below emergency_target_percent.
                     The JPEGs under storage_root/cctv/ are what actually
                     fill the disk; the DB only holds their path, so the
                     real freeing happens at os.unlink(file_path).
                     dust_inspection (sensor readings) is deliberately left
                     untouched — it is tiny and is the data we most want to
                     keep; only the heavy images are dropped under pressure.
                     Unlike run_all(), if unlink fails the DB row is left
                     intact so the next pass can retry — emergency mode
                     prioritises freeing disk space over avoiding orphans.
"""

from __future__ import annotations

import os
import shutil

import asyncpg
import structlog

from .config import CleanerSettings

log = structlog.get_logger(__name__)


_NORMAL_VIDEO_SELECT_SQL = """
    SELECT video_id, file_path FROM video
     WHERE is_valid AND NOT is_excluded
       AND captured_at < NOW() - make_interval(days => $1)
     LIMIT $2
"""

_ANOMALY_VIDEO_SELECT_SQL = """
    SELECT video_id, file_path FROM video
     WHERE (NOT is_valid OR is_excluded)
       AND captured_at < NOW() - make_interval(days => $1)
     LIMIT $2
"""

_VIDEO_DELETE_SQL = "DELETE FROM video WHERE video_id = ANY($1::uuid[])"

# Emergency purge targets cctv_frame (LOAS): the JPEG files under
# storage_root/cctv/ are what fill the disk.  Oldest-first by received_at.
# Note: video is intentionally NOT the emergency target — in loas mode the
# video table is empty, so purging it freed nothing.  dust_inspection is
# never purged here (tiny rows, the readings we want to keep).
_OLDEST_CCTV_FRAME_SELECT_SQL = """
    SELECT id, file_path FROM cctv_frame
     ORDER BY received_at ASC
     LIMIT $1
"""

_CCTV_FRAME_DELETE_SQL = "DELETE FROM cctv_frame WHERE id = ANY($1::bigint[])"

_SENSOR_DELETE_SQL = (
    "DELETE FROM sensor_sample "
    "WHERE sampled_at < NOW() - make_interval(days => $1)"
)

_INGESTION_LOG_DELETE_SQL = (
    "DELETE FROM ingestion_log "
    "WHERE created_at < NOW() - make_interval(days => $1)"
)


def _delete_count(execute_result: str) -> int:
    """Parse asyncpg execute() return string e.g. 'DELETE 17'."""
    parts = execute_result.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


async def _purge_videos(
    pool: asyncpg.Pool, days: int, batch: int, select_sql: str, label: str
) -> int:
    total = 0
    while True:
        rows = await pool.fetch(select_sql, days, batch)
        if not rows:
            break
        for r in rows:
            path = r["file_path"]
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning(
                    "unlink_failed",
                    bucket=label,
                    path=path,
                    err=str(exc),
                )
        ids = [r["video_id"] for r in rows]
        await pool.execute(_VIDEO_DELETE_SQL, ids)
        total += len(rows)
        if len(rows) < batch:
            break
    return total


async def _purge_sensor(pool: asyncpg.Pool, days: int) -> int:
    return _delete_count(await pool.execute(_SENSOR_DELETE_SQL, days))


async def _purge_ingestion_log(pool: asyncpg.Pool, days: int) -> int:
    return _delete_count(await pool.execute(_INGESTION_LOG_DELETE_SQL, days))


def _disk_percent(path: str) -> float | None:
    """Return used/total as a percent, or None if disk_usage fails."""
    try:
        total, used, _ = shutil.disk_usage(path)
    except OSError as exc:
        log.error("disk_stat_failed", path=path, err=str(exc))
        return None
    if total <= 0:
        return None
    return round(used / total * 100, 1)


async def emergency_purge(
    pool: asyncpg.Pool, settings: CleanerSettings
) -> dict[str, object]:
    """Pressure-based purge: drop oldest videos until usage is below target.

    Returns a dict with at least ``activated`` (bool).  When activated, also
    includes ``deleted``, ``unlink_failures``, ``final_pct``, etc.
    """
    threshold = settings.emergency_purge_at_percent
    target = settings.emergency_target_percent
    storage_root = settings.storage_root

    pct = _disk_percent(storage_root)
    if pct is None:
        return {"activated": False, "reason": "stat_failed"}
    if pct < threshold:
        return {
            "activated": False,
            "current_pct": pct,
            "threshold": threshold,
        }

    log.warning(
        "emergency_purge_start",
        current_pct=pct,
        threshold=threshold,
        target=target,
    )

    deleted = 0
    unlink_failures = 0
    iterations = 0
    # Safety cap: at batch_size=200, 1000 iters = 200k frames. If we still
    # haven't freed enough by then something is very wrong (e.g. files are
    # not actually on the same filesystem we are measuring).
    max_iterations = 1000

    while iterations < max_iterations:
        iterations += 1

        pct = _disk_percent(storage_root)
        if pct is None:
            log.error("emergency_purge_stat_failed_midrun")
            break
        if pct <= target:
            log.info("emergency_purge_target_reached", current_pct=pct)
            break

        rows = await pool.fetch(_OLDEST_CCTV_FRAME_SELECT_SQL, settings.batch_size)
        if not rows:
            log.error(
                "emergency_purge_no_frames_left",
                current_pct=pct,
                target=target,
            )
            break

        # Track which rows we actually freed disk for.  Unlike run_all, we
        # do NOT delete the DB row if unlink failed — emergency mode wants
        # the next pass to retry the same file, not orphan it.  The JPEG on
        # disk (file_path) is the thing that frees space; the row only holds
        # the path, so unlink MUST happen for the purge to mean anything.
        successful_ids: list[object] = []
        for r in rows:
            path = r["file_path"]
            try:
                os.unlink(path)
                successful_ids.append(r["id"])
                deleted += 1
            except FileNotFoundError:
                successful_ids.append(r["id"])
            except OSError as exc:
                log.warning(
                    "emergency_unlink_failed",
                    path=path,
                    err=str(exc),
                )
                unlink_failures += 1

        if successful_ids:
            await pool.execute(_CCTV_FRAME_DELETE_SQL, successful_ids)
        else:
            # Entire batch failed to unlink → we'd loop forever on the same
            # rows. Bail out so the next tick can try again with fresh state.
            log.error(
                "emergency_purge_no_progress",
                batch_size=len(rows),
                unlink_failures=unlink_failures,
            )
            break

    final_pct = _disk_percent(storage_root)
    result: dict[str, object] = {
        "activated": True,
        "deleted": deleted,
        "unlink_failures": unlink_failures,
        "iterations": iterations,
        "final_pct": final_pct,
        "threshold": threshold,
        "target": target,
    }
    log.warning("emergency_purge_done", **result)
    return result


async def purge_all_frames(
    pool: asyncpg.Pool, settings: CleanerSettings
) -> dict[str, object]:
    """Manual '지금 정리': delete EVERY cctv_frame NOW — JPEG on disk + DB row.

    Triggered by the admin-ui button (NOTIFY) or ``--once``.  Unlike
    emergency_purge this is NOT gated by disk usage or age: the operator
    pressed the button, so wipe all accumulated frames immediately.
    dust_inspection (sensor readings) is left intact — only the heavy
    images go.  File is unlinked before its DB row so a unlink failure
    leaves the row for a retry instead of orphaning the file.
    """
    deleted = 0
    unlink_failures = 0
    batch = settings.batch_size
    iterations = 0
    # Backstop: a permanently-unkillable file is handled by the no-progress
    # break below; this cap just bounds pathological loops.
    max_iterations = 100_000

    while iterations < max_iterations:
        iterations += 1
        rows = await pool.fetch(_OLDEST_CCTV_FRAME_SELECT_SQL, batch)
        if not rows:
            break

        successful_ids: list[object] = []
        for r in rows:
            path = r["file_path"]
            try:
                os.unlink(path)
                successful_ids.append(r["id"])
                deleted += 1
            except FileNotFoundError:
                successful_ids.append(r["id"])
            except OSError as exc:
                log.warning("manual_unlink_failed", path=path, err=str(exc))
                unlink_failures += 1

        if successful_ids:
            await pool.execute(_CCTV_FRAME_DELETE_SQL, successful_ids)
        else:
            # Whole batch failed to unlink → deleting nothing would refetch
            # the same rows forever.  Bail; next press / cycle can retry.
            log.error("manual_frame_purge_no_progress", batch_size=len(rows))
            break

    result: dict[str, object] = {
        "deleted": deleted,
        "unlink_failures": unlink_failures,
        "iterations": iterations,
    }
    log.warning("manual_frame_purge_done", **result)
    return result


async def run_all(pool: asyncpg.Pool, settings: CleanerSettings) -> dict[str, int]:
    """Apply all four retention policies and return per-bucket counts."""
    n_normal = await _purge_videos(
        pool, settings.video_normal_days, settings.batch_size,
        _NORMAL_VIDEO_SELECT_SQL, "normal",
    )
    n_anomaly = await _purge_videos(
        pool, settings.video_anomaly_days, settings.batch_size,
        _ANOMALY_VIDEO_SELECT_SQL, "anomaly",
    )
    n_sensor = await _purge_sensor(pool, settings.sensor_days)
    n_log = await _purge_ingestion_log(pool, settings.ingestion_log_days)

    counts = {
        "normal": n_normal,
        "anomaly": n_anomaly,
        "sensor_sample": n_sensor,
        "ingestion_log": n_log,
    }
    log.info("cleanup_summary", **counts)
    return counts
