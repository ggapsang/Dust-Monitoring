"""Per-connection session state for the Ingestion Gateway."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class VideoBuffer:
    """Accumulates VIDEO_CHUNK payloads until VIDEO_COMPLETE arrives.

    ``station_name`` is the wire-level identifier; ``station_id`` is the
    UUID resolved via StationRepository.lookup_by_name() and is what
    eventually goes into the ``video`` table FK.
    """

    video_id: str
    station_name: str
    station_id: Any  # uuid.UUID once resolved
    total_chunks: int
    captured_at: str | None
    amr_id: str | None = None
    amr_position: dict[str, Any] | None = None
    source_format: str | None = None
    chunks: dict[int, bytes] = field(default_factory=dict)  # chunk_seq -> binary
    total_size: int = 0

    @property
    def received_count(self) -> int:
        return len(self.chunks)

    @property
    def is_complete(self) -> bool:
        return self.received_count == self.total_chunks


@dataclass(slots=True)
class IngestionSession:
    """State tied to a single TCP connection."""

    session_id: str
    peer_addr: tuple[str, int]
    video_buffers: dict[str, VideoBuffer] = field(default_factory=dict)
    connected_at: float = field(default_factory=time.monotonic)
    last_heartbeat: float = field(default_factory=time.monotonic)


class SessionRegistry:
    """Maps transport session_id to :class:`IngestionSession`."""

    def __init__(self) -> None:
        self._sessions: dict[str, IngestionSession] = {}

    def get_or_create(
        self, session_id: str, peer_addr: tuple[str, int]
    ) -> IngestionSession:
        sess = self._sessions.get(session_id)
        if sess is None:
            sess = IngestionSession(session_id=session_id, peer_addr=peer_addr)
            self._sessions[session_id] = sess
        return sess

    def drop(self, session_id: str) -> IngestionSession | None:
        return self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)
