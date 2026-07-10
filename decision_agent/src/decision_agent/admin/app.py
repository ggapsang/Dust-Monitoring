"""FastAPI app for the Decision Agent admin page.

Single-page admin: 5-area grid (top nav / role_mapping / alarm_mapping /
decision_record browser / status bar). All actions are server-rendered or
go through small JSON endpoints. No SPA framework.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ..config import DASettings
from ..judge import Judge
from ..repository import DecisionRepository, GatewayRepository
from ..role_resolver import RoleResolver

logger = structlog.get_logger(__name__)

_HERE = Path(__file__).resolve().parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"


# ---------------------------------------------------------------------------
# Pydantic models for request bodies
# ---------------------------------------------------------------------------


class RoleMappingPatch(BaseModel):
    component_name: str = Field(min_length=1, max_length=50)


class AlarmMappingPatch(BaseModel):
    final_decision: str = Field(pattern="^(normal|caution|warning|danger)$")


class ForceDecideBody(BaseModel):
    final_decision: str = Field(pattern="^(normal|caution|warning|danger)$")


class ThresholdPatch(BaseModel):
    threshold: float


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(
    settings: DASettings,
    pool: asyncpg.Pool,
    repo: DecisionRepository,
    judge: Judge,
    role_resolver: RoleResolver,
    gateway_repo: GatewayRepository | None = None,
) -> FastAPI:
    app = FastAPI(title="Decision Agent Admin", docs_url=None, redoc_url=None)

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.mount("/admin/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ---- shared dependencies ---------------------------------------------

    def get_pool() -> asyncpg.Pool:
        return pool

    def get_repo() -> DecisionRepository:
        return repo

    def get_judge() -> Judge:
        return judge

    def get_resolver() -> RoleResolver:
        return role_resolver

    def get_settings() -> DASettings:
        return settings

    # ---- root page -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        pool: asyncpg.Pool = Depends(get_pool),
    ) -> HTMLResponse:
        # 현재 분류 임계값을 서버측에서 렌더해 input 에 직접 채운다(초기 표시 보장).
        th: dict[str, Any] = {}
        try:
            rows = await pool.fetch("SELECT key, threshold FROM classification_threshold")
            th = {r["key"]: r["threshold"] for r in rows}
        except Exception:  # noqa: BLE001
            logger.exception("threshold_load_for_index_failed")
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "title": "Decision Agent Admin",
                "th_dust": th.get("dust", ""),
                "th_static": th.get("static", ""),
                "th_dynamic": th.get("dynamic", ""),
            },
        )

    # ---- status ----------------------------------------------------------

    @app.get("/admin/api/status")
    async def status(
        repo: DecisionRepository = Depends(get_repo),
        judge: Judge = Depends(get_judge),
        resolver: RoleResolver = Depends(get_resolver),
        settings: DASettings = Depends(get_settings),
        pool: asyncpg.Pool = Depends(get_pool),
    ) -> dict[str, Any]:
        db_ok = True
        counts = {"pending": 0, "decided_last_hour": 0, "stuck": 0}
        try:
            counts = await repo.status_counts(settings.admin_stuck_after_sec)
        except Exception:  # noqa: BLE001
            logger.exception("status_db_error")
            db_ok = False
        return {
            "db_ok": db_ok,
            "pending": counts["pending"],
            "decided_last_hour": counts["decided_last_hour"],
            "stuck": counts["stuck"],
            "alarm_mapping_loaded_at": _isoformat(judge.loaded_at),
            "role_mapping_loaded_at": _isoformat(resolver.loaded_at),
            "now": _isoformat(datetime.now(timezone.utc)),
        }

    # ---- reload buttons --------------------------------------------------

    @app.post("/admin/api/reload/role-mapping")
    async def reload_role_mapping(
        resolver: RoleResolver = Depends(get_resolver),
    ) -> dict[str, Any]:
        await resolver.refresh()
        return {"ok": True, "loaded_at": _isoformat(resolver.loaded_at)}

    @app.post("/admin/api/reload/alarm-mapping")
    async def reload_alarm_mapping(
        judge: Judge = Depends(get_judge),
    ) -> dict[str, Any]:
        await judge.load()
        return {"ok": True, "loaded_at": _isoformat(judge.loaded_at)}

    # ---- role_mapping ----------------------------------------------------

    @app.get("/admin/api/role-mapping")
    async def list_role_mapping(
        resolver: RoleResolver = Depends(get_resolver),
    ) -> list[dict[str, Any]]:
        rows = await resolver.list_rows()
        return [_jsonify_row(r) for r in rows]

    @app.patch("/admin/api/role-mapping/{role}")
    async def patch_role_mapping(
        role: str,
        body: RoleMappingPatch,
        resolver: RoleResolver = Depends(get_resolver),
    ) -> dict[str, Any]:
        try:
            updated = await resolver.update_component(role, body.component_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not updated:
            raise HTTPException(status_code=404, detail=f"role not found: {role}")
        return {"ok": True, "loaded_at": _isoformat(resolver.loaded_at)}

    # ---- alarm_mapping ---------------------------------------------------

    @app.get("/admin/api/alarm-mapping")
    async def list_alarm_mapping(
        judge: Judge = Depends(get_judge),
    ) -> list[dict[str, Any]]:
        rows = await judge.list_rows()
        return [_jsonify_row(r) for r in rows]

    @app.patch("/admin/api/alarm-mapping/{mapping_id}")
    async def patch_alarm_mapping(
        mapping_id: int,
        body: AlarmMappingPatch,
        judge: Judge = Depends(get_judge),
    ) -> dict[str, Any]:
        try:
            updated = await judge.update_final(mapping_id, body.final_decision)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not updated:
            raise HTTPException(status_code=404, detail=f"id not found: {mapping_id}")
        return {"ok": True, "loaded_at": _isoformat(judge.loaded_at)}

    # ---- classification_threshold (분류 임계 — 웹UI 편집) ----------------

    @app.get("/admin/api/classification-threshold")
    async def list_thresholds(
        pool: asyncpg.Pool = Depends(get_pool),
    ) -> list[dict[str, Any]]:
        rows = await pool.fetch(
            "SELECT key, threshold, updated_at FROM classification_threshold ORDER BY key"
        )
        return [_jsonify_row(dict(r)) for r in rows]

    @app.patch("/admin/api/classification-threshold/{key}")
    async def patch_threshold(
        key: str,
        body: ThresholdPatch,
        pool: asyncpg.Pool = Depends(get_pool),
    ) -> dict[str, Any]:
        result = await pool.execute(
            "UPDATE classification_threshold SET threshold = $2, updated_at = NOW() "
            "WHERE key = $1",
            key,
            body.threshold,
        )
        # asyncpg "UPDATE n"
        try:
            n = int(result.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            n = 0
        if n == 0:
            raise HTTPException(status_code=404, detail=f"threshold key not found: {key}")
        return {"ok": True, "key": key, "threshold": body.threshold}

    # ---- decisions browser ----------------------------------------------

    @app.get("/admin/api/decisions")
    async def list_decisions(
        tab: str = Query("recent", pattern="^(recent|pending|stuck)$"),
        page: int = Query(1, ge=1),
        page_size: int = Query(100, ge=1, le=500),
        repo: DecisionRepository = Depends(get_repo),
        settings: DASettings = Depends(get_settings),
    ) -> dict[str, Any]:
        rows, total = await repo.browse(
            tab=tab,  # type: ignore[arg-type]
            page=page,
            page_size=page_size,
            stuck_after_sec=settings.admin_stuck_after_sec,
        )
        await _attach_station_labels(rows, gateway_repo)
        return {
            "tab": tab,
            "page": page,
            "page_size": page_size,
            "total": total,
            "rows": [_jsonify_row(r) for r in rows],
        }

    @app.post("/admin/api/decisions/{decision_id}/force")
    async def force_decide(
        decision_id: UUID,
        body: ForceDecideBody,
        repo: DecisionRepository = Depends(get_repo),
    ) -> dict[str, Any]:
        try:
            updated = await repo.force_decide(decision_id, body.final_decision, None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not updated:
            raise HTTPException(
                status_code=409,
                detail="record not found or already decided",
            )
        return {"ok": True}

    # ---- error envelope --------------------------------------------------

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        logger.exception("admin_unhandled_error", path=str(request.url))
        return JSONResponse(status_code=500, content={"error": "internal_error"})

    return app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _attach_station_labels(
    rows: list[dict[str, Any]],
    gateway_repo: GatewayRepository | None,
) -> None:
    """Add a ``station_label`` field to each row for the UI ``station`` column.

    Priority: 별명(label) → 'TGT-{target_id}' → 'TGT-?'.  Resolved via
    gateway_db by ``dust_id``; best-effort — any failure (or gateway_db
    disabled/unreachable) leaves every row at 'TGT-?'.
    """
    labels: dict[int, str] = {}
    if gateway_repo is not None:
        dust_ids = [r["dust_id"] for r in rows if r.get("dust_id") is not None]
        if dust_ids:
            try:
                labels = await gateway_repo.station_labels(dust_ids)
            except Exception:  # noqa: BLE001 — labels are cosmetic, never fatal
                logger.exception("station_label_lookup_failed")
    for r in rows:
        dust_id = r.get("dust_id")
        r["station_label"] = labels.get(dust_id, "TGT-?") if dust_id is not None else "TGT-?"


def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _jsonify_row(row: dict[str, Any]) -> dict[str, Any]:
    """Make a row dict JSON-safe (UUID -> str, datetime -> iso8601)."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = _isoformat(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Server entry — runs alongside the poller via asyncio.gather
# ---------------------------------------------------------------------------


async def run_admin_server(
    app: FastAPI,
    host: str,
    port: int,
    stop_event: asyncio.Event,
) -> None:
    """Run uvicorn until stop_event is set."""
    import uvicorn  # local import — keeps top-level optional

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",   # structlog handles app logs; mute uvicorn access spam
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        await stop_event.wait()
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5.0)
        except asyncio.TimeoutError:
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
