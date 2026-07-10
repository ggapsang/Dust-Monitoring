#!/usr/bin/env python3
"""LOAS Tfoi v4a — DUST(13310) + CCTV(13320) **상관(correlation) 페어** 송신 드라이버.

ingestion_gateway 의 두 리스너에 시간창(±2초) 안으로 한 쌍을 쏴서,
백그라운드 Correlator 가 cctv_frame ↔ dust_inspection 을 페어링하도록 한다.

핵심 동작 순서(중요):
  1) DUST 먼저 — WAYPOINT_ID 가 non-null 이어야
       · dust_inspection 행이 저장되고(waypoint_gate.should_collect),
       · 공유 게이트 allow_save=True 로 바뀌어 뒤따르는 CCTV 가 저장된다.
  2) --gap 초 뒤 CCTV — 같은 시각대(received_at)로 저장된다.
  3) Correlator(기본 10초 주기)가 received_at 가 [dust-2s, dust+2s] 안인
     프레임을 dust 행에 붙인다(시간창 매칭, id 매칭 아님).

와이어 포맷(서버가 기대하는 그대로):
  DUST  : 12바이트 **리틀엔디안** 헤더 ``<HHBBIH``
            sop=0xAABB, id=0xD002, ver=0x02, enc=0, timestamp(uint32), length(uint16)
          + XML 본문(<ELEMENT> ... </ELEMENT>, ≤ 1448바이트)
  CCTV  : 9바이트 헤더 [5바이트 ASCII 해상도태그][uint32 **빅엔디안** 길이] + JPEG 본문
  두 프로토콜 모두 ACK/heartbeat 없음 · 프레임당 연결 1개(connect→send→close).

사용 예:
  python3 send_loas_pair.py                      # 127.0.0.1 로 1쌍
  python3 send_loas_pair.py --count 5 --interval 1.0
  python3 send_loas_pair.py --host 10.5.20.160 --waypoint-id 10390 --target-id 1
  python3 send_loas_pair.py --dry-run            # 송신 없이 프레임 구성만 출력/검증
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from datetime import datetime, timezone

# -- LOAS 상수 (gw_proto.codec.loas.constants 와 동일) ----------------------
SOP_DUST = 0xAABB
DOID_DUST_INSPECTION = 0xD002
PROTOCOL_VERSION = 0x02
DUST_MAX_BODY = 1448
# DUST 헤더는 리틀엔디안(실측). CCTV 길이는 빅엔디안(!I).
_DUST_HEADER = struct.Struct("<HHBBIH")
_CCTV_LEN = struct.Struct("!I")
CCTV_TAGS = {"V1080", "V720p", "V640p"}


def build_dust_frame(args, *, ts_epoch: int) -> bytes:
    """DUST_INSPECTION_INFOR XML 1건 → 12바이트 헤더 + 본문 프레임."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    # 파서(parse_dust_inspection)가 읽는 태그를 모두 채운 현실적 본문.
    body_xml = (
        "<ELEMENT>"
        "<CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>"
        f"<DATETIME>{now_iso}</DATETIME>"
        f"<DUST_DATA>{args.dust_value}</DUST_DATA>"
        f"<DUST_ALARM>{args.dust_alarm}</DUST_ALARM>"
        f"<SENSOR_TYPE>{args.sensor_type}</SENSOR_TYPE>"
        f"<SENSOR_INDEX>{args.sensor_index}</SENSOR_INDEX>"
        f"<TARGET_INDEX>{args.target_index}</TARGET_INDEX>"
        f"<WAYPOINT_X>{args.wx}</WAYPOINT_X>"
        f"<WAYPOINT_Y>{args.wy}</WAYPOINT_Y>"
        f"<WAYPOINT_Z>{args.wz}</WAYPOINT_Z>"
        "<ROT_X>0.0</ROT_X><ROT_Y>0.0</ROT_Y><ROT_Z>0.0</ROT_Z><ROT_W>1.0</ROT_W>"
        f"<UGV_ID>{args.ugv_id}</UGV_ID>"
        f"<LOCATION_ID>{args.location_id}</LOCATION_ID>"
        f"<MAP_ID>{args.map_id}</MAP_ID>"
        f"<NAVIGATION_ID>{args.navigation_id}</NAVIGATION_ID>"
        f"<EXEC_ID>{args.exec_id}</EXEC_ID>"
        f"<PLANT_ID>{args.plant_id}</PLANT_ID>"
        f"<TARGET_ID>{args.target_id}</TARGET_ID>"
        f"<WAYPOINT_ID>{args.waypoint_id}</WAYPOINT_ID>"
        f"<INSPECTION_LOCAL_ID>{args.inspection_local_id}</INSPECTION_LOCAL_ID>"
        f"<OBJECT_ID>{args.object_id}</OBJECT_ID>"
        f"<MISSION_ID>{args.mission_id}</MISSION_ID>"
        "<INSPECTION_PAN>0</INSPECTION_PAN>"
        "<INSPECTION_TILT>0</INSPECTION_TILT>"
        "<INSPECTION_LIFT>0</INSPECTION_LIFT>"
        "</ELEMENT>"
    )
    body = body_xml.encode("utf-8")
    if len(body) > DUST_MAX_BODY:
        raise SystemExit(f"DUST 본문 {len(body)}B > 최대 {DUST_MAX_BODY}B")
    header = _DUST_HEADER.pack(
        SOP_DUST, DOID_DUST_INSPECTION, PROTOCOL_VERSION, 0, ts_epoch, len(body)
    )
    return header + body


def build_cctv_frame(args) -> bytes:
    """해상도 태그 + 빅엔디안 길이 + (최소 JPEG 매직) 본문."""
    if args.resolution not in CCTV_TAGS:
        raise SystemExit(f"해상도 태그는 {sorted(CCTV_TAGS)} 중 하나여야 함")
    n = max(6, args.cctv_bytes)
    body = b"\xff\xd8\xff\xe0" + b"X" * (n - 6) + b"\xff\xd9"  # FFD8..FFD9
    header = args.resolution.encode("ascii") + _CCTV_LEN.pack(len(body))
    return header + body


def _send(host: str, port: int, payload: bytes) -> None:
    """프레임당 연결 1개: connect → send → close (AMR 동작과 동일)."""
    with socket.create_connection((host, port), timeout=5.0) as s:
        s.sendall(payload)


def send_pair(args, i: int) -> None:
    ts_epoch = int(time.time())
    dust = build_dust_frame(args, ts_epoch=ts_epoch)
    cctv = build_cctv_frame(args)

    if args.dry_run:
        print(f"[{i}] DRY-RUN  dust={len(dust)}B (hdr12+body{len(dust)-12}) "
              f"cctv={len(cctv)}B (hdr9+body{len(cctv)-9})  "
              f"waypoint_id={args.waypoint_id} target_id={args.target_id}")
        print(f"      dust_hdr_hex={dust[:12].hex()}  cctv_hdr_hex={cctv[:9].hex()}")
        return

    # 1) DUST 먼저 → 저장 + allow_save=True
    _send(args.host, args.dust_port, dust)
    # 2) gap 후 CCTV → 저장 (같은 시간창)
    time.sleep(args.gap)
    _send(args.host, args.cctv_port, cctv)
    print(f"[{i}] sent  DUST→{args.host}:{args.dust_port} ({len(dust)}B)  "
          f"CCTV→{args.host}:{args.cctv_port} ({len(cctv)}B)  "
          f"waypoint_id={args.waypoint_id} target_id={args.target_id}")


def main() -> None:
    p = argparse.ArgumentParser(description="LOAS DUST+CCTV 상관 페어 송신기")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--dust-port", type=int, default=13310)
    p.add_argument("--cctv-port", type=int, default=13320)
    p.add_argument("--gap", type=float, default=0.2,
                   help="DUST→CCTV 간격(초). ±2초 창 안이어야 페어링됨")
    p.add_argument("--count", type=int, default=1, help="송신할 쌍 개수")
    p.add_argument("--interval", type=float, default=1.0, help="쌍 사이 간격(초)")
    p.add_argument("--dry-run", action="store_true", help="송신 없이 구성만 출력")
    # DUST 식별/관측 값 (저장·개소·FK 에 필요한 것들)
    p.add_argument("--waypoint-id", type=int, default=10390,
                   help="non-null 이어야 저장+CCTV 게이트 통과")
    p.add_argument("--target-id", type=int, default=1, help="관측 개소 식별 기준")
    p.add_argument("--target-index", type=int, default=1)
    p.add_argument("--plant-id", type=int, default=1)
    p.add_argument("--dust-value", type=float, default=35.2)
    p.add_argument("--dust-alarm", type=int, default=3)
    p.add_argument("--sensor-type", type=int, default=1)
    p.add_argument("--sensor-index", type=int, default=13)
    p.add_argument("--ugv-id", type=int, default=1)
    p.add_argument("--location-id", type=int, default=1)
    p.add_argument("--map-id", type=int, default=1)
    p.add_argument("--navigation-id", type=int, default=1039)
    p.add_argument("--exec-id", type=int, default=1001)
    p.add_argument("--object-id", type=int, default=1)
    p.add_argument("--mission-id", type=int, default=1)
    p.add_argument("--inspection-local-id", type=int, default=1000001)
    p.add_argument("--wx", type=float, default=12.345)
    p.add_argument("--wy", type=float, default=67.890)
    p.add_argument("--wz", type=float, default=0.0)
    # CCTV
    p.add_argument("--resolution", default="V640p", choices=sorted(CCTV_TAGS))
    p.add_argument("--cctv-bytes", type=int, default=4000)
    args = p.parse_args()

    for i in range(1, args.count + 1):
        try:
            send_pair(args, i)
        except (OSError, SystemExit) as exc:
            print(f"[{i}] 실패: {exc}", file=sys.stderr)
        if i < args.count:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
