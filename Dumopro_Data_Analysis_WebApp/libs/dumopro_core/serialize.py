from __future__ import annotations

import json
from datetime import datetime, timezone

from .models import CandleStats


def candle_to_json(stats: CandleStats, *, updated_at: datetime | None = None) -> str:
    payload = stats.model_dump()
    if updated_at is not None:
        payload["updated_at"] = updated_at.astimezone(timezone.utc).isoformat()
    elif stats.updated_at is not None:
        payload["updated_at"] = stats.updated_at.astimezone(timezone.utc).isoformat()
    return json.dumps(payload, separators=(",", ":"))


def candle_from_json(raw: str) -> dict:
    return json.loads(raw)
