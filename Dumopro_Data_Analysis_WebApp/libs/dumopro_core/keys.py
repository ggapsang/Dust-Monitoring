from typing import Literal

Unit = Literal["hour", "day", "week", "month"]
Target = Literal["median", "max", "q3"]


def live_raw(station: str, unit: Unit, bucket_key: str) -> str:
    return f"live:raw:{station}:{unit}:{bucket_key}"


def live_stats(station: str, unit: Unit, bucket_key: str) -> str:
    return f"live:stats:{station}:{unit}:{bucket_key}"


def frozen(station: str, unit: Unit, bucket_key: str) -> str:
    return f"frozen:{station}:{unit}:{bucket_key}"


def frozen_index(station: str, unit: Unit) -> str:
    return f"frozen:index:{station}:{unit}"


def cursor(station: str) -> str:
    return f"cursor:{station}"


def residual(station: str, unit: Unit, target: Target) -> str:
    return f"residual:{station}:{unit}:{target}"


def channel_candle(station: str) -> str:
    return f"channel:candle:{station}"


def config_runtime() -> str:
    return "config:runtime"


def stations_list() -> str:
    return "stations:list"


def stations_removed() -> str:
    """Hash {station_name -> last_known_station_id}.  Records the UUID
    of stations that were removed (or simply stopped) so that when a
    station with the same name reappears with a different UUID, the
    reconciler can recognise it as a re-registration even though the
    old StationTask has already been torn down."""
    return "stations:removed"


def pending_conflict(station: str) -> str:
    """Hash {old_id, new_id, detected_at} for a re-registered station
    awaiting user decision (carry_over / start_fresh)."""
    return f"pending_conflict:{station}"


def pending_conflict_pattern() -> str:
    return "pending_conflict:*"


# Pub/sub channel: API publishes here when [Sync now] is clicked,
# poller subscribes and runs an immediate reconcile.
STATION_SYNC_CHANNEL = "channel:station-sync"
