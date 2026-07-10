"""Send a dummy video as VIDEO_CHUNKs + VIDEO_COMPLETE.

Usage:
    python scripts/send_video.py <station_name> [chunks] [host] [port]

Defaults: chunks=3  host=127.0.0.1  port=9000
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

from gw_proto import (
    Message,
    MessageType,
    StandardCodec,
    TcpClient,
    VideoChunkMeta,
    build_video_chunk_payload,
)


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    station_name = sys.argv[1]
    total_chunks = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    host = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
    port = int(sys.argv[4]) if len(sys.argv) > 4 else 9000

    video_id = str(uuid.uuid4())
    captured_at = datetime.now(timezone.utc).isoformat()

    client = TcpClient(host, port, StandardCodec())
    await client.connect()
    print(f"Connected to {host}:{port}  video_id={video_id}")

    # Send chunks
    for seq in range(total_chunks):
        meta = VideoChunkMeta(
            video_id=video_id,
            chunk_seq=seq,
            total_chunks=total_chunks,
            station_name=station_name,
            captured_at=captured_at,
        )
        body = os.urandom(1024)                         # 1 KiB per chunk
        payload = build_video_chunk_payload(meta, body)
        await client.send(Message(msg_type=MessageType.VIDEO_CHUNK, payload=payload))
        resp = await asyncio.wait_for(client.receive(), timeout=5.0)
        print(f"  chunk {seq + 1}/{total_chunks} -> 0x{int(resp.msg_type):04X}")

    # VIDEO_COMPLETE
    complete_payload = json.dumps({"video_id": video_id}).encode()
    await client.send(Message(msg_type=MessageType.VIDEO_COMPLETE, payload=complete_payload))
    resp = await asyncio.wait_for(client.receive(), timeout=5.0)
    print(f"VIDEO_COMPLETE -> 0x{int(resp.msg_type):04X} metadata={resp.metadata}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
