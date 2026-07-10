"""SENSOR_SAMPLE message handler."""

from __future__ import annotations

from datetime import datetime

import structlog
from gw_proto import Message

from ..repository import (
    IngestionLogRepository,
    SensorRepository,
    StationRepository,
    StationRequestRepository,
)
from ..session import IngestionSession

logger = structlog.get_logger(__name__)


class SensorHandler:
    def __init__(
        self,
        sensor_repo: SensorRepository,
        station_repo: StationRepository,
        log_repo: IngestionLogRepository,
        request_repo: StationRequestRepository,
    ) -> None:
        self._sensor_repo = sensor_repo
        self._station_repo = station_repo
        self._log_repo = log_repo
        self._request_repo = request_repo

    async def handle(
        self, message: Message, session: IngestionSession
    ) -> Message:
        if not isinstance(message.metadata, dict):
            await self._log_repo.insert(
                station_id=None, message_type="SENSOR_SAMPLE",
                status="error", error_message="Invalid payload (no metadata)",
            )
            return Message.error("Invalid sensor payload")

        data = message.metadata
        station_name = data.get("station_name")
        if not station_name:
            await self._log_repo.insert(
                station_id=None, message_type="SENSOR_SAMPLE",
                status="error", error_message="Missing station_name",
            )
            return Message.error("Missing station_name")
        station_name = str(station_name)

        station_id = await self._station_repo.lookup_by_name(station_name)
        if station_id is None:
            await self._request_repo.upsert(station_name)
            await self._log_repo.insert(
                station_id=None, message_type="SENSOR_SAMPLE",
                status="error",
                error_message=f"Unknown or inactive station: {station_name}",
            )
            return Message.error(f"Unknown station: {station_name}")

        try:
            sampled_at = datetime.fromisoformat(
                str(data["sampled_at"]).replace("Z", "+00:00")
            )
            await self._sensor_repo.insert(
                station_id=station_id,
                measurement_type=str(data["measurement_type"]),
                value=float(data["value"]),
                unit=str(data["unit"]),
                sampled_at=sampled_at,
            )
        except (KeyError, ValueError, TypeError) as exc:
            await self._log_repo.insert(
                station_id=station_id, message_type="SENSOR_SAMPLE",
                status="error", error_message=f"Bad payload: {exc}",
            )
            return Message.error(f"Bad sensor payload: {exc}")

        logger.debug(
            "sensor_sample_stored",
            station_name=station_name,
            measurement_type=data.get("measurement_type"),
        )
        return Message.ack()
