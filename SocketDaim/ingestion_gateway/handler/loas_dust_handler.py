"""LOAS DUST frame handler.

Receives a (header, body, peer) tuple from :class:`LoasDustTcpServer`,
decodes the XML body, and inserts one ``dust_inspection`` row.  All
failure modes degrade to a log entry — one bad frame must not stall the
stream (the spec is one-way push, so we have no way to ask the peer to
retry).

The raw XML text is also persisted to the ``raw_xml`` column on every
accepted frame.  An *optional* on-disk dump can be enabled via
``IGW_LOAS_DUST_DUMP_ENABLED=true`` for off-DB inspection; the dump rate
is throttled by ``IGW_LOAS_DUST_DUMP_INTERVAL_SEC`` so a multi-day
deployment does not fill the disk at 1 fps.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import structlog
from gw_proto.codec.loas.dust_framing import DustHeader
from gw_proto.codec.loas.dust_xml import DustInspectionPayload, decode_xml, parse_dust_inspection
from gw_proto.codec.loas.errors import LoasXmlError

from ..repository import DustInspectionRepository
from . import waypoint_gate as gate

logger = structlog.get_logger(__name__)


class LoasDustHandler:
    def __init__(
        self,
        repo: DustInspectionRepository,
        *,
        dump_dir: Path | None = None,
        dump_interval_sec: float = 0.0,
    ) -> None:
        """``dump_dir`` is ``None`` when the file dump is disabled (the default);
        when set, every accepted XML is also written to disk subject to
        ``dump_interval_sec`` throttling."""
        self._repo = repo
        self._dump_dir = dump_dir
        self._dump_interval = max(0.0, dump_interval_sec)
        # Monotonic-ish wall-clock of the last successful dump.  No lock
        # needed — each connection is handled in its own coroutine and the
        # interval check is a best-effort throttle, not an invariant.
        self._last_dump_epoch: float = 0.0
        # [중간처리용] 직전 프레임 payload.  현재 규칙(waypoint_id)에는 쓰이지 않지만,
        # 이력 기반 규칙(직전과 비교 등)으로 바꿀 때를 대비해 보관해 둔다.
        self._prev: Any | None = None

    async def on_frame(
        self,
        hdr: DustHeader,
        body: bytes,
        peer: tuple[str, int],
    ) -> None:
        # 1. Decode body once.  parse_dust_inspection() also calls
        #    decode_xml internally, so we duplicate a cheap decode here
        #    in order to keep the original text for audit storage.
        try:
            raw_text = decode_xml(body)
        except LoasXmlError as exc:
            logger.warning(
                "loas_dust_decode_failed",
                peer=peer, body_len=len(body), err=str(exc),
            )
            return

        # 2. Parse into a structured payload.
        try:
            payload = parse_dust_inspection(body)
        except LoasXmlError as exc:
            logger.warning(
                "loas_dust_parse_failed",
                peer=peer, body_len=len(body), err=str(exc),
            )
            return

        # 2.5 [중간처리] AMR 관측/촬영 중일 때만 수집, 주행 중엔 스킵 — **활성**.
        #     정확 조건: waypoint_id·target_id 모두 유효(!=NULL, !=0) → 수집 / 하나라도 NULL·0 → 스킵.
        #     판정 규칙은 waypoint_gate.should_collect() 한 곳에 모여 있다(규칙 변경 시
        #     그 함수만 수정, 핸들러 불변).  비활성화하려면 `if True:` → `if False:`
        #     (그러면 allow_save 기본 True 유지 → 전부 수집).
        if True:
            collect = gate.should_collect(payload, self._prev)  # payload 전체 전달
            self._prev = payload           # 직전 payload 보관(이력 규칙 대비)
            gate.allow_save = collect      # CCTV 저장 게이트도 같은 결정으로 동기화
            gate.target_id = payload.target_id  # CCTV 파일명용 target_id 공유(없으면 None→'NA')
            if not collect:
                logger.debug("loas_dust_skipped_driving", peer=peer, cmd_id=payload.cmd_id)
                return                     # 주행 중(waypoint_id=NULL) → DUST 스킵
            # 관측/촬영(waypoint_id != NULL) → 계속 영속화

        # 3. Persist.
        try:
            row_id = await self._repo.insert(hdr, payload, raw_xml=raw_text)
        except Exception:
            logger.exception(
                "loas_dust_insert_failed",
                peer=peer, cmd_id=payload.cmd_id, ugv_id=payload.ugv_id,
            )
            return

        # 4. Optional on-disk dump (best effort; never blocks the DB path).
        if self._dump_dir is not None:
            try:
                await self._maybe_dump(raw_text, payload)
            except Exception:
                logger.exception("loas_dust_dump_failed", peer=peer)

        logger.debug(
            "loas_dust_stored",
            row_id=row_id,
            ugv_id=payload.ugv_id,
            mission_id=payload.mission_id,
            dust_alarm=payload.dust_alarm,
            dust_value=payload.dust_data,
        )

    # -- helpers -----------------------------------------------------------

    async def _maybe_dump(
        self,
        xml_text: str,
        payload: DustInspectionPayload,
    ) -> None:
        """Write ``xml_text`` to disk subject to the configured throttle.

        Filename pattern (sortable, collision-free at 1 fps):
            ``{epoch_us}_wp{waypoint_id}.xml``
        Directory layout (per-hour, keeps directories bounded):
            ``{dump_dir}/{YYYY-MM-DD}/{HH}/...``
        """
        now = datetime.now(timezone.utc)
        now_epoch = now.timestamp()
        if (
            self._dump_interval > 0
            and now_epoch - self._last_dump_epoch < self._dump_interval
        ):
            return
        self._last_dump_epoch = now_epoch

        date_dir = now.strftime("%Y-%m-%d")
        hour_dir = now.strftime("%H")
        epoch_us = int(now_epoch * 1_000_000)
        wp = payload.waypoint_id if payload.waypoint_id is not None else "NA"
        path = self._dump_dir / date_dir / hour_dir / f"{epoch_us}_wp{wp}.xml"

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(xml_text)

        await asyncio.to_thread(_write)
