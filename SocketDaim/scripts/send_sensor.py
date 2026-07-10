"""Send a single SENSOR_SAMPLE to the Ingestion Gateway.

Usage (from SocketDaim root, socketdaim env activated):

    python scripts/send_sensor.py <station_name> [value] [host] [port]

Defaults:  value=23.5  host=127.0.0.1  port=9000
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone

from gw_proto import Message, MessageType, StandardCodec, TcpClient


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    station_name = sys.argv[1]
    value = float(sys.argv[2]) if len(sys.argv) > 2 else 23.5
    host = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
    port = int(sys.argv[4]) if len(sys.argv) > 4 else 9000

    payload = json.dumps({
        "station_name": station_name,
        "measurement_type": "temperature",
        "value": value,
        "unit": "C",
        "sampled_at": datetime.now(timezone.utc).isoformat(),
    }).encode()

    client = TcpClient(host, port, StandardCodec())
    await client.connect()
    print(f"Connected to {host}:{port}")

    await client.send(Message(msg_type=MessageType.SENSOR_SAMPLE, payload=payload))
    print(f"Sent SENSOR_SAMPLE (station={station_name}, value={value})")

    response = await asyncio.wait_for(client.receive(), timeout=5.0)
    print(f"Response: type=0x{int(response.msg_type):04X} metadata={response.metadata}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
