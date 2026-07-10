"""Codec package – factory for protocol codec selection.

Only the ``standard`` protocol uses the :class:`Codec` interface.  Vendor
protocols (e.g. LOAS Tfoi v4a — see :mod:`gw_proto.codec.loas`) have
service-specific framing that doesn't fit the ``encode/decode`` pair, and
are wired into the application layer directly instead of through
:func:`get_codec`.
"""

from __future__ import annotations

from .base import Codec
from .standard import StandardCodec

__all__ = ["Codec", "StandardCodec", "get_codec"]


def get_codec(protocol: str = "standard") -> StandardCodec:
    """Return a codec instance for the named protocol.

    Currently only ``"standard"`` is dispatched here.  Vendor protocols
    (``"loas"``, future entries) are handled by their own transports in
    :mod:`ingestion_gateway.main` and do not return a Codec.
    """
    if protocol == "standard":
        return StandardCodec()
    raise ValueError(
        f"Unknown or non-Codec protocol: {protocol!r} "
        "(only 'standard' returns a Codec; vendor protocols are wired "
        "directly in the application layer)"
    )
