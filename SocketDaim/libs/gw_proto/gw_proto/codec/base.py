"""Codec protocol – the single swap-point for vendor protocol migration."""

from __future__ import annotations

from typing import Protocol

from ..messages import Message, MessageType


class Codec(Protocol):
    """Encode / decode gateway messages.

    Gateway code depends only on this interface.  At runtime the concrete
    implementation is selected by the ``PROTOCOL`` environment variable
    (``standard`` or ``vendor``).
    """

    def encode(self, message: Message) -> tuple[MessageType, bytes]:
        """Serialize *message* into ``(type_code, raw_payload)``."""
        ...

    def decode(self, msg_type: MessageType, payload: bytes) -> Message:
        """Deserialize *payload* into a :class:`Message`."""
        ...
