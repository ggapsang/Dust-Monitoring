"""FastAPI app factory for the SocketDaim Admin UI.

Single-page admin: 5-area grid (top nav / left side panels / main panel /
status bar). All actions go through small JSON endpoints under
`/admin/api/*`. The HTML root is rendered by Jinja2.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import asyncpg
import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import AdminUISettings
from .repository import (
    StationAdminRepository,
    StationRequestAdminRepository,
    VideoAdminRepository,
    WaypointLabelAdminRepository,
)
from .repository.video_repo import end_of_day, parse_bool_tri, parse_date, parse_sort

logger = structlog.get_logger(__name__)

_HERE = Path(__file__).resolve().parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"


def _disk_usage(path: str) -> dict[str, Any]:
    """shutil.disk_usage 결과를 JSON-safe dict로 변환.
    mount point가 없거나 권한 문제로 실패하면 ok=False 반환."""
    try:
        total, used, free = shutil.disk_usage(path)
    except OSError:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0, "ok": False}
    return {
        "total_gb": round(total / 1e9, 2),
        "used_gb":  round(used  / 1e9, 2),
        "free_gb":  round(free  / 1e9, 2),
        "percent":  round(used / total * 100, 1) if total else 0,
        "ok": True,
    }


# ---------------------------------------------------------------------------
# Pydantic body models
# ---------------------------------------------------------------------------


_STATION_STATUS_PATTERN = "^(collecting|waiting|training|inferring|inactive)$"


class StationCreate(BaseModel):
    station_name: str = Field(min_length=1, max_length=255)
    location_info: str | None = None
    amr_id: str | None = Field(default=None, max_length=128)
    capture_cycle: int | None = Field(default=None, ge=1, le=86400)
    description: str | None = None
    status: str = Field(default="collecting", pattern=_STATION_STATUS_PATTERN)


class StationPatch(BaseModel):
    station_name: str | None = Field(default=None, min_length=1, max_length=255)
    location_info: str | None = None
    amr_id: str | None = Field(default=None, max_length=128)
    capture_cycle: int | None = Field(default=None, ge=1, le=86400)
    description: str | None = None
    status: str | None = Field(default=None, pattern=_STATION_STATUS_PATTERN)


class RequestApprove(BaseModel):
    location_info: str | None = None
    amr_id: str | None = Field(default=None, max_length=128)
    capture_cycle: int | None = Field(default=None, ge=1, le=86400)
    description: str | None = None
    notes: str | None = None


class RequestReject(BaseModel):
    notes: str | None = None


class VideoLabelPatch(BaseModel):
    is_valid: bool | None = None
    is_excluded: bool | None = None


class WaypointLabelUpsert(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    location: str | None = None
    notes: str | None = None
    # 개소 기준값(audit + display) — UI 에서 전달
    target_id: int | None = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(
    settings: AdminUISettings,
    pool: asyncpg.Pool,
    station_repo: StationAdminRepository,
    request_repo: StationRequestAdminRepository,
    video_repo: VideoAdminRepository,
    waypoint_repo: WaypointLabelAdminRepository,
) -> FastAPI:
    app = FastAPI(title="SocketDaim Admin", docs_url=None, redoc_url=None)

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.mount("/admin/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    storage_root_resolved = Path(settings.storage_root).resolve()

    def get_pool() -> asyncpg.Pool:
        return pool

    def get_station_repo() -> StationAdminRepository:
        return station_repo

    def get_request_repo() -> StationRequestAdminRepository:
        return request_repo

    def get_video_repo() -> VideoAdminRepository:
        return video_repo

    def get_waypoint_repo() -> WaypointLabelAdminRepository:
        return waypoint_repo

    # ---- HTML root --------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "index.html", {"title": "SocketDaim 관리"}
        )

    # ---- status -----------------------------------------------------------

    @app.get("/admin/api/status")
    async def status(
        sr: StationAdminRepository = Depends(get_station_repo),
        rr: StationRequestAdminRepository = Depends(get_request_repo),
        pool: asyncpg.Pool = Depends(get_pool),
    ) -> dict[str, Any]:
        db_ok = True
        st_counts: dict[str, int] = {"active": 0, "inactive": 0, "total": 0}
        rq_counts: dict[str, int] = {
            "pending": 0, "rejected": 0, "approved": 0, "total_attempts": 0,
        }
        database_mb = 0.0
        try:
            await pool.fetchval("SELECT 1")
            st_counts = await sr.status_counts()
            rq_counts = await rr.status_counts()
            db_size = await pool.fetchval("SELECT pg_database_size(current_database())")
            if db_size is not None:
                database_mb = round(float(db_size) / 1024 / 1024, 1)
        except Exception:
            logger.exception("status_db_error")
            db_ok = False

        disk = _disk_usage(settings.storage_root)
        return {
            "db_ok": db_ok,
            "active": st_counts["active"],
            "inactive": st_counts["inactive"],
            "stations_total": st_counts["total"],
            "pending": rq_counts["pending"],
            "rejected": rq_counts["rejected"],
            "approved": rq_counts["approved"],
            "total_attempts": rq_counts["total_attempts"],
            "disk": disk,
            "disk_warn_percent": settings.disk_warn_percent,
            "disk_critical_percent": settings.disk_critical_percent,
            "database_mb": database_mb,
            "now": datetime.now(timezone.utc).isoformat(),
        }

    # ---- stations ---------------------------------------------------------

    @app.get("/admin/api/stations")
    async def list_stations(
        sr: StationAdminRepository = Depends(get_station_repo),
    ) -> list[dict[str, Any]]:
        rows = await sr.list_all()
        return [_jsonify_row(r) for r in rows]

    @app.post("/admin/api/stations", status_code=201)
    async def create_station(
        body: StationCreate,
        sr: StationAdminRepository = Depends(get_station_repo),
    ) -> dict[str, Any]:
        row = await sr.create(
            station_name=body.station_name,
            location_info=body.location_info,
            amr_id=body.amr_id,
            capture_cycle=body.capture_cycle,
            description=body.description,
            status=body.status,
        )
        return _jsonify_row(row)

    @app.patch("/admin/api/stations/{station_id}")
    async def patch_station(
        station_id: UUID,
        body: StationPatch,
        sr: StationAdminRepository = Depends(get_station_repo),
    ) -> dict[str, Any]:
        try:
            row = await sr.update(
                station_id,
                station_name=body.station_name,
                location_info=body.location_info,
                amr_id=body.amr_id,
                capture_cycle=body.capture_cycle,
                description=body.description,
                status=body.status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if row is None:
            raise HTTPException(status_code=404, detail="station not found")
        return _jsonify_row(row)

    @app.delete("/admin/api/stations/{station_id}")
    async def delete_station(
        station_id: UUID,
        sr: StationAdminRepository = Depends(get_station_repo),
    ) -> dict[str, Any]:
        try:
            ok = await sr.delete(station_id)
        except asyncpg.ForeignKeyViolationError:
            raise HTTPException(
                status_code=409,
                detail="이 개소에 video/sensor_sample 데이터가 있어 삭제할 수 없습니다",
            )
        if not ok:
            raise HTTPException(status_code=404, detail="station not found")
        return {"ok": True}

    # ---- requests ---------------------------------------------------------

    @app.get("/admin/api/requests")
    async def list_requests(
        status: Literal["pending", "approved", "rejected", "all"] = Query("pending"),
        rr: StationRequestAdminRepository = Depends(get_request_repo),
    ) -> list[dict[str, Any]]:
        rows = await rr.list_by_status(status)
        return [_jsonify_row(r) for r in rows]

    @app.post("/admin/api/requests/{station_name}/approve")
    async def approve_request(
        station_name: str,
        body: RequestApprove,
        rr: StationRequestAdminRepository = Depends(get_request_repo),
    ) -> dict[str, Any]:
        # Ensure the request still exists and is in 'pending' before approving.
        existing = await rr.get(station_name)
        if existing is None:
            raise HTTPException(status_code=404, detail="request not found")
        if existing["status"] == "approved":
            raise HTTPException(status_code=409, detail="이미 승인된 요청입니다")
        try:
            row = await rr.approve(
                station_name,
                location_info=body.location_info,
                amr_id=body.amr_id,
                capture_cycle=body.capture_cycle,
                description=body.description,
                notes=body.notes,
            )
        except asyncpg.UniqueViolationError as exc:
            # station_name already taken (concurrent approve / duplicate)
            raise HTTPException(status_code=409, detail=str(exc))
        return _jsonify_row(row)

    @app.post("/admin/api/requests/{station_name}/reject")
    async def reject_request(
        station_name: str,
        body: RequestReject,
        rr: StationRequestAdminRepository = Depends(get_request_repo),
    ) -> dict[str, Any]:
        ok = await rr.reject(station_name, notes=body.notes)
        if not ok:
            raise HTTPException(status_code=404, detail="request not found")
        return {"ok": True}

    @app.post("/admin/api/requests/{station_name}/restore")
    async def restore_request(
        station_name: str,
        rr: StationRequestAdminRepository = Depends(get_request_repo),
    ) -> dict[str, Any]:
        ok = await rr.restore(station_name)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail="rejected request not found",
            )
        return {"ok": True}

    # ---- admin actions ----------------------------------------------------

    @app.post("/admin/api/seed-samples")
    async def seed_samples(
        sr: StationAdminRepository = Depends(get_station_repo),
    ) -> dict[str, int]:
        return await sr.seed_samples()

    @app.post("/admin/api/cleanup/trigger")
    async def trigger_cleanup(
        pool: asyncpg.Pool = Depends(get_pool),
    ) -> dict[str, Any]:
        # sd-cleaner LISTENs on this channel; the NOTIFY wakes it up and
        # makes it run the retention sweep immediately, without disturbing
        # the regular 03:00 KST schedule. Returns as soon as the NOTIFY is
        # queued — the actual cleanup runs in the cleaner container.
        await pool.execute("NOTIFY cleanup_trigger, 'admin_ui'")
        logger.info("cleanup_trigger_sent")
        return {"ok": True}

    # ---- videos (라벨링) ---------------------------------------------------

    @app.get("/admin/api/videos")
    async def list_videos(
        sort: str = Query("captured_at:desc"),
        page: int = Query(1, ge=1),
        size: int = Query(50, ge=1, le=500),
        q_id8: str | None = Query(None, max_length=8),
        q_station_id: UUID | None = Query(None),
        q_station_name: str | None = Query(None, max_length=255),
        q_amr_id: str | None = Query(None, max_length=128),
        q_format: str | None = Query(None, pattern="^(mp4|jpeg|jpeg_seq|raw)$"),
        q_resolution: str | None = Query(None, max_length=32),
        q_from: str | None = Query(None),
        q_to: str | None = Query(None),
        q_valid: str | None = Query(None),
        q_excluded: str | None = Query(None),
        q_duration_min: float | None = Query(None, ge=0),
        q_duration_max: float | None = Query(None, ge=0),
        vr: VideoAdminRepository = Depends(get_video_repo),
    ) -> dict[str, Any]:
        try:
            sort_col, sort_dir = parse_sort(sort)
            filters = {
                "id8":           q_id8 or None,
                "station_id":    q_station_id,
                "station_name":  q_station_name or None,
                "amr_id":        q_amr_id or None,
                "format":        q_format or None,
                "resolution":    q_resolution or None,
                "from_dt":       parse_date(q_from),
                "to_dt":         end_of_day(q_to),
                "is_valid":      parse_bool_tri(q_valid),
                "is_excluded":   parse_bool_tri(q_excluded),
                "duration_min":  q_duration_min,
                "duration_max":  q_duration_max,
            }
            rows, total = await vr.list_paged(
                sort_col=sort_col, sort_dir=sort_dir,
                filters=filters, page=page, size=size,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # file size를 응답에 포함 — storage가 ro로 마운트되어 있어도 stat은 가능.
        out_rows: list[dict[str, Any]] = []
        for r in rows:
            d = _jsonify_row(r)
            d["size_bytes"] = _safe_stat_size(r["file_path"], storage_root_resolved)
            out_rows.append(d)
        return {
            "rows": out_rows,
            "total": total,
            "page": page,
            "size": size,
            "sort": f"{sort_col}:{sort_dir}",
        }

    @app.patch("/admin/api/videos/{video_id}")
    async def patch_video_labels(
        video_id: UUID,
        body: VideoLabelPatch,
        vr: VideoAdminRepository = Depends(get_video_repo),
    ) -> dict[str, Any]:
        try:
            row = await vr.patch_labels(
                video_id,
                is_valid=body.is_valid,
                is_excluded=body.is_excluded,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except asyncpg.InsufficientPrivilegeError as exc:
            logger.warning("video_patch_missing_grant", err=str(exc))
            raise HTTPException(
                status_code=403,
                detail="gw_admin 롤에 UPDATE 권한이 없습니다. "
                       "migrate_005_video_admin_update.sql을 적용해 주세요.",
            )
        if row is None:
            raise HTTPException(status_code=404, detail="video not found")
        d = _jsonify_row(row)
        d["size_bytes"] = _safe_stat_size(row["file_path"], storage_root_resolved)
        return d

    @app.get("/admin/api/videos/{video_id}/stream")
    async def stream_video(
        video_id: UUID,
        vr: VideoAdminRepository = Depends(get_video_repo),
    ) -> FileResponse:
        row = await vr.get(video_id)
        if row is None:
            raise HTTPException(status_code=404, detail="video not found")
        path = _safe_resolve_path(row["file_path"], storage_root_resolved)
        if not path.exists():
            raise HTTPException(status_code=404, detail="file not found on disk")
        return FileResponse(str(path), media_type=_mime_for(row["source_format"]))

    @app.get("/admin/api/videos/{video_id}/download")
    async def download_video(
        video_id: UUID,
        vr: VideoAdminRepository = Depends(get_video_repo),
    ) -> FileResponse:
        row = await vr.get(video_id)
        if row is None:
            raise HTTPException(status_code=404, detail="video not found")
        path = _safe_resolve_path(row["file_path"], storage_root_resolved)
        if not path.exists():
            raise HTTPException(status_code=404, detail="file not found on disk")
        ext = _ext_for(row["source_format"])
        filename = f"{row['video_id']}.{ext}"
        return FileResponse(
            str(path),
            media_type="application/octet-stream",
            filename=filename,
        )

    # ---- waypoints (LOAS mode) -------------------------------------------
    # LOAS 모드의 "발견된 관측 개소" 목록 + 사람 친화 라벨 CRUD.
    # 개소 식별자 = target_id 합성 station_id (UUID).  같은 target_id = 같은 개소.

    @app.get("/admin/api/waypoints")
    async def list_waypoints(
        wr: WaypointLabelAdminRepository = Depends(get_waypoint_repo),
    ) -> list[dict[str, Any]]:
        rows = await wr.list_discovered()
        return [_jsonify_row(r) for r in rows]

    @app.put("/admin/api/waypoints/{station_id}")
    async def upsert_waypoint_label(
        station_id: UUID,
        body: WaypointLabelUpsert,
        wr: WaypointLabelAdminRepository = Depends(get_waypoint_repo),
    ) -> dict[str, Any]:
        row = await wr.upsert(
            station_id=station_id,
            target_id=body.target_id,
            label=body.label,
            location=body.location,
            notes=body.notes,
        )
        return _jsonify_row(row)

    @app.delete("/admin/api/waypoints/{station_id}")
    async def delete_waypoint_label(
        station_id: UUID,
        wr: WaypointLabelAdminRepository = Depends(get_waypoint_repo),
    ) -> dict[str, bool]:
        ok = await wr.delete(station_id)
        if not ok:
            raise HTTPException(status_code=404, detail="label not found")
        return {"ok": True}

    # ---- recent ingestion activity (LOAS dust + cctv merged) -------------

    @app.get("/admin/api/recent-activity")
    async def recent_activity(
        limit: int = Query(30, ge=1, le=100),
        wr: WaypointLabelAdminRepository = Depends(get_waypoint_repo),
    ) -> list[dict[str, Any]]:
        rows = await wr.recent_activity(limit)
        return [_jsonify_row(r) for r in rows]

    # ---- recent raw DUST XML (toggle-on debugging panel) ----------------

    @app.get("/admin/api/recent-dust-xml")
    async def recent_dust_xml(
        limit: int = Query(10, ge=1, le=50),
        wr: WaypointLabelAdminRepository = Depends(get_waypoint_repo),
    ) -> list[dict[str, Any]]:
        rows = await wr.recent_dust_xml(limit)
        return [_jsonify_row(r) for r in rows]

    # ---- recent errors (sidebar log panel) -------------------------------

    @app.get("/admin/api/recent-errors")
    async def recent_errors(
        limit: int = Query(20, ge=1, le=100),
        sr: StationAdminRepository = Depends(get_station_repo),
    ) -> list[dict[str, Any]]:
        rows = await sr.recent_errors(limit)
        return [_jsonify_row(r) for r in rows]

    # ---- error envelope ---------------------------------------------------

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        logger.exception("admin_unhandled_error", path=str(request.url))
        return JSONResponse(status_code=500, content={"detail": "internal_error"})

    return app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _jsonify_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = _isoformat(v)
        else:
            out[k] = v
    return out


_MIME_BY_FORMAT: dict[str | None, str] = {
    "mp4":      "video/mp4",
    "jpeg":     "image/jpeg",
    "jpeg_seq": "image/jpeg",
    "raw":      "application/octet-stream",
}

_EXT_BY_FORMAT: dict[str | None, str] = {
    "mp4":      "mp4",
    "jpeg":     "jpg",
    "jpeg_seq": "jpg",
    "raw":      "bin",
}


def _mime_for(source_format: str | None) -> str:
    return _MIME_BY_FORMAT.get(source_format, "application/octet-stream")


def _ext_for(source_format: str | None) -> str:
    return _EXT_BY_FORMAT.get(source_format, "bin")


def _safe_resolve_path(file_path: str, storage_root: Path) -> Path:
    """video.file_path가 storage_root 안에 있는지 확인하고 Path를 돌려준다.

    이상한 file_path(과거 마이그레이션 사고, 손상된 row 등)로 storage_root 바깥을
    가리키는 경우 path traversal 가드로 403을 던진다.
    """
    p = Path(file_path).resolve()
    try:
        p.relative_to(storage_root)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="file_path가 storage_root 바깥을 가리킵니다",
        )
    return p


def _safe_stat_size(file_path: str, storage_root: Path) -> int | None:
    """파일 크기를 bytes로 돌려준다. 실패 시 None.

    storage_root 바깥 또는 파일 없음 등은 모두 None.  list 응답에서 행마다
    호출되므로 예외를 흘리지 않는다.
    """
    try:
        p = Path(file_path).resolve()
        p.relative_to(storage_root)
        return os.stat(p).st_size
    except (OSError, ValueError):
        return None
