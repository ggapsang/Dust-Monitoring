"""Control message handlers (HEARTBEAT, ERROR)."""

from __future__ import annotations

import json
import time

import structlog
from gw_proto import Message

from ..repository import IngestionLogRepository
from ..session import IngestionSession

logger = structlog.get_logger(__name__)


class ControlHandler:
    def __init__(self, log_repo: IngestionLogRepository) -> None:
        self._log_repo = log_repo

    async def handle_heartbeat(
        self, message: Message, session: IngestionSession
    ) -> Message:
        session.last_heartbeat = time.monotonic()
        logger.debug("heartbeat_received", session_id=session.session_id[:8])
        return Message.ack()

    async def handle_error(
        self, message: Message, session: IngestionSession
    ) -> None:
        """Peer reported an error – log it and return no response."""
        reason: str
        if message.metadata and isinstance(message.metadata, dict):
            reason = str(message.metadata.get("error", ""))
        else:
            try:
                reason = message.payload.decode("utf-8", errors="replace")
            except Exception:
                reason = repr(message.payload)

        logger.warning(
            "peer_error",
            session_id=session.session_id[:8],
            reason=reason,
        )
        await self._log_repo.insert(
            station_id=None,
            message_type="ERROR",
            status="error",
            error_message=reason,
        )
