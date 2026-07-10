"""SQLite-backed durable outbox for the Egress Gateway.

Stores messages that have been queued for transmission to LOAS.
On Ack we DELETE the row; on restart we drain remaining rows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    outbox_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id  TEXT     NOT NULL UNIQUE,
    msg_type     INTEGER  NOT NULL,
    payload      BLOB     NOT NULL,
    attempts     INTEGER  NOT NULL DEFAULT 0,
    created_at   TEXT     NOT NULL
);
"""


@dataclass(slots=True)
class OutboxRow:
    decision_id: str
    msg_type: int
    payload: bytes
    attempts: int


class Outbox:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        Path(os.path.dirname(self._path) or ".").mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def add(
        self, decision_id: str, msg_type: int, payload: bytes
    ) -> None:
        """Idempotent insert; duplicate decision_id is ignored."""
        assert self._db is not None
        await self._db.execute(
            "INSERT OR IGNORE INTO outbox (decision_id, msg_type, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (decision_id, int(msg_type), payload, datetime.now(timezone.utc).isoformat()),
        )
        await self._db.commit()

    async def remove(self, decision_id: str) -> None:
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM outbox WHERE decision_id = ?", (decision_id,)
        )
        await self._db.commit()

    async def bump_attempts(self, decision_id: str) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE outbox SET attempts = attempts + 1 WHERE decision_id = ?",
            (decision_id,),
        )
        await self._db.commit()

    async def iter_pending(self) -> AsyncIterator[OutboxRow]:
        """Yield all rows currently waiting to be sent (FIFO by outbox_id)."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT decision_id, msg_type, payload, attempts "
            "FROM outbox ORDER BY outbox_id"
        ) as cursor:
            async for row in cursor:
                yield OutboxRow(
                    decision_id=row[0],
                    msg_type=row[1],
                    payload=row[2],
                    attempts=row[3],
                )

    async def count(self) -> int:
        assert self._db is not None
        async with self._db.execute("SELECT COUNT(*) FROM outbox") as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
