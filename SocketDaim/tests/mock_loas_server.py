"""Minimal LOAS receiver for Egress verification.

Listens on TCP, accepts ANALYSIS_RESULT / ALERT / HEARTBEAT messages from
the Egress Gateway, replies with ACK, and logs the payload.  Used only
for development/testing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from gw_proto import (
    Message,
    MessageType,
    SessionContext,
    StandardCodec,
    TcpServer,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mock_loas")


async def _handler(message: Message, ctx: SessionContext) -> Message | None:
    if message.msg_type == MessageType.HEARTBEAT:
        return Message.ack()

    if message.msg_type in (MessageType.ANALYSIS_RESULT, MessageType.ALERT):
        try:
            body = json.loads(message.payload.decode("utf-8")) if message.payload else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"<unparsable>": True}
        log.info(
            "received type=0x%04X decision_id=%s station_id=%s final=%s",
            int(message.msg_type),
            body.get("decision_id"),
            body.get("station_id"),
            body.get("final_decision"),
        )
        return Message.ack()

    if message.msg_type == MessageType.ERROR:
        log.warning("received ERROR from peer: %s", message.metadata)
        return None

    log.warning("unexpected msg type 0x%04X", int(message.msg_type))
    return Message.error(f"Unexpected: 0x{int(message.msg_type):04X}")


async def _main() -> None:
    host = os.environ.get("MOCK_LOAS_HOST", "0.0.0.0")
    port = int(os.environ.get("MOCK_LOAS_PORT", "9001"))
    log.info("Mock LOAS server listening on %s:%s", host, port)
    server = TcpServer(host=host, port=port, codec=StandardCodec(), handler=_handler)
    await server.start()


if __name__ == "__main__":
    asyncio.run(_main())
