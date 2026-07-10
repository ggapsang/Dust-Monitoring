"""Video labeling repository (gw_admin role).

화이트리스트 dict로 query string column 이름 → 실제 SQL identifier를 매핑한다.
필터 값은 항상 parameterized binding ($1, $2, ...)로 전달되므로 사용자 입력에
의한 SQL injection이 차단된다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import asyncpg

# query string의 sort col 값 → 실제 SQL identifier 매핑.
# 이 dict 밖의 컬럼 이름은 절대 SQL에 박히지 않는다.
SORTABLE_COLUMNS: dict[str, str] = {
    "video_id":      "v.video_id",
    "station_name":  "s.station_name",
    "station_id":    "v.station_id",
    "amr_id":        "v.amr_id",
    "captured_at":   "v.captured_at",
    "duration_sec":  "v.duration_sec",
    "resolution":    "v.resolution",
    "source_format": "v.source_format",
    "is_valid":      "v.is_valid",
    "is_excluded":   "v.is_excluded",
    "created_at":    "v.created_at",
}

_SORTABLE_DIRS = frozenset({"asc", "desc"})

_BASE_SELECT = """
SELECT v.video_id, v.station_id, s.station_name, v.amr_id,
       v.captured_at, v.file_path, v.duration_sec, v.resolution,
       v.source_format, v.amr_position, v.quality_check_result,
       v.is_valid, v.is_excluded, v.created_at
  FROM video v
  JOIN station s ON s.station_id = v.station_id
"""

_BASE_COUNT = """
SELECT COUNT(*)
  FROM video v
  JOIN station s ON s.station_id = v.station_id
"""


class VideoAdminRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_paged(
        self,
        *,
        sort_col: str,
        sort_dir: str,
        filters: dict[str, Any],
        page: int,
        size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if sort_col not in SORTABLE_COLUMNS:
            raise ValueError(f"invalid sort column: {sort_col}")
        if sort_dir not in _SORTABLE_DIRS:
            raise ValueError(f"invalid sort dir: {sort_dir}")
        sql_col = SORTABLE_COLUMNS[sort_col]

        where: list[str] = []
        args: list[Any] = []

        def add(tmpl: str, value: Any) -> None:
            args.append(value)
            where.append(tmpl.format(n=len(args)))

        if filters.get("station_id") is not None:
            add("v.station_id = ${n}", filters["station_id"])
        if filters.get("station_name"):
            add("s.station_name ILIKE '%' || ${n} || '%'", filters["station_name"])
        if filters.get("amr_id"):
            add("v.amr_id ILIKE '%' || ${n} || '%'", filters["amr_id"])
        if filters.get("format"):
            add("v.source_format = ${n}", filters["format"])
        if filters.get("resolution"):
            add("v.resolution ILIKE '%' || ${n} || '%'", filters["resolution"])
        if filters.get("from_dt") is not None:
            add("v.captured_at >= ${n}", filters["from_dt"])
        if filters.get("to_dt") is not None:
            add("v.captured_at < ${n}", filters["to_dt"])
        if filters.get("is_valid") is not None:
            add("v.is_valid = ${n}", filters["is_valid"])
        if filters.get("is_excluded") is not None:
            add("v.is_excluded = ${n}", filters["is_excluded"])
        if filters.get("duration_min") is not None:
            add("v.duration_sec >= ${n}", filters["duration_min"])
        if filters.get("duration_max") is not None:
            add("v.duration_sec <= ${n}", filters["duration_max"])
        if filters.get("id8"):
            add("v.video_id::text ILIKE ${n} || '%'", filters["id8"])

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        order_sql = f" ORDER BY {sql_col} {sort_dir.upper()} NULLS LAST"
        if sort_col != "video_id":
            order_sql += ", v.video_id DESC"  # 안정 정렬

        count_args = list(args)  # LIMIT/OFFSET 추가 전 복사
        offset = max(0, (page - 1) * size)
        args.append(size)
        args.append(offset)
        list_sql = (
            _BASE_SELECT + where_sql + order_sql
            + f" LIMIT ${len(args) - 1} OFFSET ${len(args)}"
        )
        count_sql = _BASE_COUNT + where_sql

        async with self._pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                total = await conn.fetchval(count_sql, *count_args)
                rows = await conn.fetch(list_sql, *args)
        return [dict(r) for r in rows], int(total or 0)

    async def get(self, video_id: uuid.UUID) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            _BASE_SELECT + " WHERE v.video_id = $1",
            video_id,
        )
        return dict(row) if row else None

    async def patch_labels(
        self,
        video_id: uuid.UUID,
        *,
        is_valid: bool | None,
        is_excluded: bool | None,
    ) -> dict[str, Any] | None:
        if is_valid is None and is_excluded is None:
            raise ValueError("at least one of is_valid/is_excluded must be set")
        updated = await self._pool.fetchrow(
            """
            UPDATE video
               SET is_valid    = COALESCE($2, is_valid),
                   is_excluded = COALESCE($3, is_excluded)
             WHERE video_id = $1
         RETURNING video_id
            """,
            video_id, is_valid, is_excluded,
        )
        if updated is None:
            return None
        # station JOIN한 full row를 클라이언트로 응답하기 위해 재조회.
        return await self.get(video_id)


def parse_sort(raw: str | None) -> tuple[str, str]:
    """`<col>:<dir>` 형식의 query string을 (col, dir)로 분리하고 검증한다.

    잘못된 값은 ValueError를 던지므로 호출 측에서 400으로 변환한다.
    raw가 None/빈 문자열이면 default (captured_at, desc) 반환.
    """
    if not raw:
        return "captured_at", "desc"
    if ":" not in raw:
        raise ValueError(f"sort must be 'col:dir', got: {raw!r}")
    col, _, direction = raw.partition(":")
    col = col.strip()
    direction = direction.strip().lower()
    if col not in SORTABLE_COLUMNS:
        raise ValueError(f"invalid sort column: {col!r}")
    if direction not in _SORTABLE_DIRS:
        raise ValueError(f"invalid sort direction: {direction!r}")
    return col, direction


def parse_bool_tri(raw: str | None) -> bool | None:
    """'true'/'false'/'' (또는 None) → True/False/None."""
    if raw is None or raw == "":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError(f"expected 'true'/'false'/'', got {raw!r}")


def parse_date(raw: str | None) -> datetime | None:
    """ISO 'YYYY-MM-DD' → tz-aware datetime (UTC midnight). None 허용."""
    if not raw:
        return None
    # date.fromisoformat 사용 후 UTC midnight로 변환
    from datetime import date, time, timezone
    d = date.fromisoformat(raw)
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def end_of_day(raw: str | None) -> datetime | None:
    """`q_to`는 inclusive로 받아 다음 날 00:00 UTC로 변환."""
    from datetime import date, time, timedelta, timezone
    if not raw:
        return None
    d = date.fromisoformat(raw)
    return datetime.combine(d + timedelta(days=1), time.min, tzinfo=timezone.utc)
