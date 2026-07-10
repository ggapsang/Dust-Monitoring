"""VIDEO_CHUNK + VIDEO_COMPLETE message handlers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import structlog
from gw_proto import Message, parse_video_chunk

from ..repository import (
    IngestionLogRepository,
    StationRepository,
    StationRequestRepository,
    VideoRepository,
)
from ..session import IngestionSession, VideoBuffer

logger = structlog.get_logger(__name__)

DEFAULT_EXTENSION = "bin"


class VideoHandler:
    def __init__(
        self,
        video_repo: VideoRepository,
        station_repo: StationRepository,
        log_repo: IngestionLogRepository,
        request_repo: StationRequestRepository,
        storage_root: str,
    ) -> None:
        self._video_repo = video_repo
        self._station_repo = station_repo
        self._log_repo = log_repo
        self._request_repo = request_repo
        self._storage_root = Path(storage_root)

    # -- VIDEO_CHUNK -------------------------------------------------------

    async def handle_chunk(
        self, message: Message, session: IngestionSession
    ) -> Message:
        try:
            meta, binary_body = parse_video_chunk(message.payload)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            await self._log_repo.insert(
                station_id=None, message_type="VIDEO_CHUNK",
                status="error", error_message=f"Malformed chunk: {exc}",
            )
            return Message.error(f"Malformed video chunk: {exc}")

        buf = session.video_buffers.get(meta.video_id)
        if buf is None:
            # First chunk: resolve station_name → station_id (UUID).
            station_id = await self._station_repo.lookup_by_name(meta.station_name)
            if station_id is None:
                await self._request_repo.upsert(meta.station_name)
                await self._log_repo.insert(
                    station_id=None, message_type="VIDEO_CHUNK",
                    status="error",
                    error_message=f"Unknown or inactive station: {meta.station_name}",
                )
                return Message.error(f"Unknown station: {meta.station_name}")

            buf = VideoBuffer(
                video_id=meta.video_id,
                station_name=meta.station_name,
                station_id=station_id,
                total_chunks=meta.total_chunks,
                captured_at=meta.captured_at,
                amr_id=meta.amr_id,
                amr_position=meta.amr_position,
                source_format=meta.source_format,
            )
            session.video_buffers[meta.video_id] = buf

        buf.chunks[meta.chunk_seq] = binary_body
        buf.total_size += len(binary_body)

        logger.debug(
            "video_chunk_buffered",
            video_id=meta.video_id,
            chunk_seq=meta.chunk_seq,
            received=buf.received_count,
            total=buf.total_chunks,
        )
        return Message.ack()

    # -- VIDEO_COMPLETE ----------------------------------------------------

    async def handle_complete(
        self, message: Message, session: IngestionSession
    ) -> Message:
        if not isinstance(message.metadata, dict):
            return Message.error("Invalid VIDEO_COMPLETE payload")

        video_id = message.metadata.get("video_id")
        if not video_id:
            return Message.error("Missing video_id in VIDEO_COMPLETE")

        buf = session.video_buffers.get(video_id)
        if buf is None:
            await self._log_repo.insert(
                station_id=None, message_type="VIDEO_COMPLETE",
                status="error",
                error_message=f"No chunks buffered for video_id={video_id}",
            )
            return Message.error(f"No buffered video: {video_id}")

        if not buf.is_complete:
            await self._log_repo.insert(
                station_id=buf.station_id, message_type="VIDEO_COMPLETE",
                status="error",
                error_message=(
                    f"Incomplete: received {buf.received_count}/{buf.total_chunks}"
                ),
            )
            return Message.error(
                f"Incomplete: {buf.received_count}/{buf.total_chunks} chunks"
            )

        try:
            file_path = await asyncio.to_thread(self._assemble_and_save, buf)
            captured_at = _parse_iso(buf.captured_at)
            await self._video_repo.insert(
                video_id=buf.video_id,
                station_id=buf.station_id,
                file_path=str(file_path),
                captured_at=captured_at,
                amr_id=buf.amr_id,
                amr_position=buf.amr_position,
                source_format=buf.source_format,
            )
        except Exception as exc:
            logger.exception(
                "video_save_failed",
                video_id=buf.video_id,
                station_id=buf.station_id,
            )
            await self._log_repo.insert(
                station_id=buf.station_id, message_type="VIDEO_COMPLETE",
                status="error", error_message=f"Save failed: {exc}",
            )
            session.video_buffers.pop(video_id, None)
            return Message.error(f"Video save failed: {exc}")

        logger.info(
            "video_stored",
            video_id=buf.video_id,
            station_id=buf.station_id,
            total_size=buf.total_size,
            chunks=buf.total_chunks,
            file_path=str(file_path),
        )
        session.video_buffers.pop(video_id, None)
        return Message.ack()

    # -- internal ----------------------------------------------------------

    def _assemble_and_save(self, buf: VideoBuffer) -> Path:
        """Concatenate chunks in order and write to the storage volume."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dir_path = self._storage_root / "videos" / str(buf.station_id) / today
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{buf.video_id}.{DEFAULT_EXTENSION}"

        with open(file_path, "wb") as f:
            for seq in sorted(buf.chunks.keys()):
                f.write(buf.chunks[seq])
        return file_path


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
