"""Standard codec – temporary implementation until vendor spec is finalised.

Control messages use JSON (UTF-8).  Video chunks use JSON header + newline +
raw binary (section 4.3).
"""

from __future__ import annotations

import json
from typing import Any

from ..messages import Message, MessageType


class StandardCodec:
    """Length-prefixed framing + JSON / binary payload codec."""

    # Message types whose payload is JSON header + \\n + binary body
    _BINARY_TYPES = frozenset({MessageType.VIDEO_CHUNK})

    # ---- encode ----------------------------------------------------------

    def encode(self, message: Message) -> tuple[MessageType, bytes]:
        """Return ``(msg_type, raw_payload)`` ready for framing."""
        return message.msg_type, message.payload

    # ---- decode ----------------------------------------------------------

    def decode(self, msg_type: MessageType, payload: bytes) -> Message:
        """Construct a :class:`Message` from raw payload bytes."""
        metadata: dict[str, Any] | None = None

        if msg_type in self._BINARY_TYPES:
            # JSON header + \n + binary body
            sep = payload.find(b"\n")
            if sep != -1:
                try:
                    metadata = json.loads(payload[:sep])
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        else:
            # Pure JSON payload (or empty)
            if payload:
                try:
                    metadata = json.loads(payload)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

        return Message(msg_type=msg_type, payload=payload, metadata=metadata)
