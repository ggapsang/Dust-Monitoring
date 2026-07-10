"""LOAS CCTV frame handler.

Receives a (jpg_bytes, resolution, peer) tuple from
:class:`LoasCctvTcpServer`, writes the JPG to disk, and inserts one
``cctv_frame`` row.

Failure ordering:

1. Build path + write file (thread-pool, no event-loop blocking).
2. If file write fails → log + drop frame (no DB row created).
3. Insert row.  ``received_at`` 은 파일명 타임스탬프와 **같은 인스턴트**다
   (파일명/경로는 KST(UTC+9) 표기, DB ``received_at`` 은 UTC; TIMESTAMPTZ 라 동일 시각).
4. If insert fails → unlink the orphan file (best effort) + log.

Crashes between steps 2 and 3 can leave orphan files; that case is
expected to be cleaned up by sd-cleaner in a follow-up PR.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from ..repository import CctvFrameRepository
from . import waypoint_gate as gate

logger = structlog.get_logger(__name__)


class LoasCctvHandler:
    def __init__(
        self,
        repo: CctvFrameRepository,
        storage_root: Path,
        amr_id: str,
        *,
        subdir: str = "cctv",
    ) -> None:
        self._repo = repo
        self._root = Path(storage_root) / subdir
        self._amr_id = amr_id

    async def on_frame(
        self,
        jpg: bytes,
        resolution: str,
        peer: tuple[str, int],
    ) -> None:
        # [조건부 저장] DUST 게이트가 저장을 불허하면 스킵한다.
        # DUST 중간처리 블럭이 **활성**(if True)이라, AMR 이 주행 중(waypoint_id=NULL)이면
        # allow_save=False 가 되어 그 구간 영상은 저장하지 않고, 관측/촬영 중
        # (waypoint_id != NULL)일 때만 저장한다.
        if not gate.allow_save:
            logger.debug("loas_cctv_skipped_by_gate", peer=peer, resolution=resolution)
            return  # 게이트 저장 불허 → 파일/DB 저장 모두 스킵

        now = datetime.now(timezone.utc)
        # 파일명용 target_id 는 가장 최근 DUST 결정(waypoint_gate)에서 가져온다.
        # 관측 구간에만 CCTV 가 저장되므로 그때 현재 관측 대상과 일치한다(없으면 'NA').
        path = self._build_path(now, gate.target_id)

        # Step 1: write file
        try:
            await self._write_atomically(path, jpg)
        except OSError as exc:
            logger.warning(
                "loas_cctv_write_failed",
                peer=peer, path=str(path), err=str(exc),
            )
            return

        # Step 2: insert row.  On failure, undo the file write.
        try:
            row_id = await self._repo.insert(
                received_at=now,
                amr_id=self._amr_id,
                source_ip=peer[0],
                resolution=resolution,
                file_path=str(path),
                byte_size=len(jpg),
            )
        except Exception:
            logger.exception(
                "loas_cctv_insert_failed",
                peer=peer, path=str(path),
            )
            try:
                os.unlink(path)
            except OSError:
                logger.warning(
                    "loas_cctv_orphan_unlink_failed",
                    path=str(path),
                )
            return

        logger.debug(
            "loas_cctv_stored",
            row_id=row_id,
            resolution=resolution,
            byte_size=len(jpg),
            path=str(path),
        )

    # -- helpers -----------------------------------------------------------

    def _build_path(self, ts: datetime, target_id: int | None) -> Path:
        """``{root}/{amr_id}/{YYYY-MM-DD}/{HH}/{TARGET_ID}_{yyyymmddHHMMSS}_{sss}.jpg``.

        파일명 = ``{target_id}_{yyyymmddHHMMSS}_{밀리초3자리}.jpg`` (밀리초는 ``_`` 로 구분).
        target_id 는 가장 최근 DUST 결정(waypoint_gate.target_id)에서 오며, 관측 구간에만
        CCTV 가 저장되므로 그때 현재 관측 대상과 일치한다.  값을 알 수 없으면 ``NA`` 로
        시작한다(모든 예외 안전).  per-hour 서브디렉터리는 디렉터리당 파일 수를 억제한다.

        타임스탬프(날짜/시/파일명)는 **KST(UTC+9)** 로 표기한다.  ``ts`` 는 UTC-aware 이므로
        같은 인스턴트를 KST 로 변환한다(DB ``received_at`` 은 UTC 그대로 — TIMESTAMPTZ 라 동일 시각).
        """
        ts = ts.astimezone(timezone(timedelta(hours=9)))         # → KST 표기
        date_dir = ts.strftime("%Y-%m-%d")
        hour_dir = ts.strftime("%H")
        ms = ts.microsecond // 1000                              # 0~999 (밀리초)
        stamp = ts.strftime("%Y%m%d%H%M%S") + f"_{ms:03d}"        # yyyymmddHHMMSS_sss (KST)
        tid = target_id if target_id is not None else "NA"
        return self._root / self._amr_id / date_dir / hour_dir / f"{tid}_{stamp}.jpg"

    async def _write_atomically(self, path: Path, body: bytes) -> None:
        """Create parent dirs and write the JPG body in a thread."""
        import asyncio

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                f.write(body)

        await asyncio.to_thread(_write)
